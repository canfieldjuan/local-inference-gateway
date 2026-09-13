from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from .config import Credential, CredentialStore, Settings, read_private_token
from .contracts import (
    PROTOCOL_VERSION,
    AcknowledgementRequest,
    InferenceRequest,
    is_bounded_root_object_choice,
    parse_json_object,
    safe_request_id,
)
from .store import (
    AcknowledgementConflict,
    RequestCapacityLimited,
    RequestExpired,
    RequestForbidden,
    RequestIdentityConflict,
    RequestNotTerminal,
    RequestRecord,
    RequestStore,
    RequestUnknown,
    ResultCipher,
    StoreError,
)
from .worker import (
    FallbackWorker,
    InferenceWorker,
    InvalidWorkerOutput,
    LMStudioWorker,
    OllamaWorker,
    WorkerOutcomeAmbiguous,
    WorkerUnavailable,
)

TASK_POLICY_VERSION = 1


@dataclass(frozen=True)
class TaskPolicy:
    temperature: float
    max_output_tokens: int
    allow_root_object_choice: bool = False


TASK_POLICIES = MappingProxyType(
    {
        ("document.summary.step", 1): TaskPolicy(
            temperature=0.0,
            max_output_tokens=4_096,
            allow_root_object_choice=True,
        ),
        ("email.analyze", 1): TaskPolicy(temperature=0.1, max_output_tokens=1_500),
        ("email.schedule.extract", 1): TaskPolicy(
            temperature=0.1,
            max_output_tokens=1_500,
        ),
        ("invoice.extract.batch", 1): TaskPolicy(
            temperature=0.0,
            max_output_tokens=12_288,
        ),
    }
)


@dataclass(frozen=True)
class GatewayFailure(RuntimeError):
    code: str
    retryable: bool
    status_code: int
    retry_after_seconds: int | None = None


@dataclass
class _ActiveAttempt:
    request_id: str
    finished: threading.Event


class GatewayService:
    def __init__(
        self,
        settings: Settings,
        store: RequestStore,
        worker: InferenceWorker,
        clock: Callable[[], datetime],
    ):
        self.settings = settings
        self.store = store
        self.worker = worker
        self.clock = clock
        self._lane_lock = threading.Lock()
        self._active: _ActiveAttempt | None = None

    def health(self, credential: Credential) -> dict[str, object]:
        authorized_tasks = sorted(task for task in TASK_POLICIES if task in credential.tasks)
        tasks = []
        for task_id, task_version in authorized_tasks:
            available = self.worker.health((task_id, task_version))
            tasks.append(
                {
                    "id": task_id,
                    "version": task_version,
                    "status": "available" if available else "unavailable",
                    "diagnostic_code": "ready" if available else "worker_unavailable",
                }
            )
        return {"protocol_version": PROTOCOL_VERSION, "tasks": tasks}

    def infer(self, credential: Credential, request: InferenceRequest) -> dict[str, object]:
        now = self.clock()
        self._validate_policy(credential, request, now)
        digest = request.canonical_digest()
        try:
            record = self.store.admit(request, credential.identity_hash, digest, now)
        except RequestCapacityLimited as exc:
            raise GatewayFailure(
                "capacity_limited", True, 429, self.settings.retry_after_seconds
            ) from exc
        except RequestExpired as exc:
            raise GatewayFailure("request_expired", False, 409) from exc
        except RequestForbidden as exc:
            raise GatewayFailure("forbidden", False, 403) from exc
        except RequestIdentityConflict as exc:
            raise GatewayFailure("invalid_request", False, 409) from exc
        response = self._existing_response(record)
        if response is not None:
            return response
        if record.state == "in_progress":
            return self._join_active(record, credential, digest)
        if record.state == "ambiguous":
            raise GatewayFailure("inference_timeout", True, 504, self.settings.retry_after_seconds)
        if record.state != "reserved":
            raise GatewayFailure("invalid_request", False, 409)

        active, owner = self._claim_lane(record.request_id)
        if not owner:
            if active.request_id == record.request_id:
                return self._wait_and_reload(active, credential, digest, request.expires_at)
            raise GatewayFailure("capacity_limited", True, 429, self.settings.retry_after_seconds)

        attempt_id = str(uuid.uuid4())
        try:
            current = self.clock()
            if request.expires_at <= current:
                self.store.get_owned(record.request_id, credential.identity_hash, digest, current)
                raise GatewayFailure("request_expired", False, 409)
            self.store.mark_in_progress(record.request_id, attempt_id, current)
            dispatch_at = self.clock()
            if request.expires_at <= dispatch_at:
                self.store.get_owned(
                    record.request_id,
                    credential.identity_hash,
                    digest,
                    dispatch_at,
                )
                raise GatewayFailure("request_expired", False, 409)
            timeout = min(
                self.settings.worker_timeout_seconds,
                max(0.001, (request.expires_at - dispatch_at).total_seconds()),
            )
            try:
                result = self.worker.infer(request, timeout)
            except WorkerUnavailable as exc:
                self.store.reset_reserved(record.request_id, attempt_id, self.clock())
                raise GatewayFailure(
                    "worker_unavailable", True, 503, self.settings.retry_after_seconds
                ) from exc
            except WorkerOutcomeAmbiguous as exc:
                self.store.mark_ambiguous(record.request_id, attempt_id, self.clock())
                raise GatewayFailure(
                    "inference_timeout", True, 504, self.settings.retry_after_seconds
                ) from exc
            except InvalidWorkerOutput as exc:
                self.store.mark_failed(
                    record.request_id,
                    attempt_id,
                    "invalid_worker_output",
                    False,
                    None,
                    self.clock(),
                )
                raise GatewayFailure("invalid_worker_output", False, 502) from exc
            except Exception as exc:
                self.store.mark_ambiguous(record.request_id, attempt_id, self.clock())
                raise GatewayFailure(
                    "inference_timeout", True, 504, self.settings.retry_after_seconds
                ) from exc
            if not self.store.complete(
                record.request_id,
                attempt_id,
                result.media_type,
                result.content,
                deployment_id=self.settings.deployment_id,
                task_policy_version=TASK_POLICY_VERSION,
                now=self.clock(),
            ):
                raise GatewayFailure("request_expired", False, 409)
            completed = self.store.get_owned(
                record.request_id, credential.identity_hash, digest, self.clock()
            )
            response = self._existing_response(completed)
            if response is None:
                raise GatewayFailure("invalid_worker_output", False, 500)
            return response
        except GatewayFailure:
            raise
        except StoreError as exc:
            self.store.mark_ambiguous(record.request_id, attempt_id, self.clock())
            raise GatewayFailure(
                "inference_timeout", True, 504, self.settings.retry_after_seconds
            ) from exc
        finally:
            self._release_lane(active)

    def acknowledge(
        self, credential: Credential, request: AcknowledgementRequest
    ) -> dict[str, object]:
        try:
            record = self.store.acknowledge(
                request.request_id,
                credential.identity_hash,
                request.disposition,
                self.clock(),
            )
        except RequestForbidden as exc:
            raise GatewayFailure("forbidden", False, 403) from exc
        except RequestUnknown as exc:
            raise GatewayFailure("unknown_request", False, 404) from exc
        except RequestExpired as exc:
            raise GatewayFailure("request_expired", False, 409) from exc
        except RequestNotTerminal as exc:
            raise GatewayFailure("result_not_terminal", False, 409) from exc
        except AcknowledgementConflict as exc:
            raise GatewayFailure("acknowledgement_conflict", False, 409) from exc
        return {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": record.request_id,
            "status": "acknowledged",
            "disposition": record.acknowledgement_disposition,
        }

    def _validate_policy(
        self, credential: Credential, request: InferenceRequest, now: datetime
    ) -> None:
        task = (request.task.id, request.task.version)
        if task not in credential.tasks:
            raise GatewayFailure("forbidden", False, 403)
        policy = TASK_POLICIES.get(task)
        if policy is None:
            raise GatewayFailure("unsupported_task", False, 422)
        if request.expires_at <= now:
            raise GatewayFailure("request_expired", False, 409)
        maximum_expiry = now + timedelta(seconds=self.settings.request_max_lifetime_seconds)
        if maximum_expiry.microsecond:
            maximum_expiry = maximum_expiry.replace(microsecond=0) + timedelta(seconds=1)
        if request.expires_at > maximum_expiry:
            raise GatewayFailure("invalid_request", False, 422)
        if request.generation.temperature != policy.temperature:
            raise GatewayFailure("unsupported_task", False, 422)
        if request.requirements.max_output_tokens > policy.max_output_tokens:
            raise GatewayFailure("unsupported_task", False, 422)
        if (
            is_bounded_root_object_choice(request.generation.response_schema)
            and not policy.allow_root_object_choice
        ):
            raise GatewayFailure("unsupported_task", False, 422)

    def _existing_response(self, record: RequestRecord) -> dict[str, object] | None:
        if record.state == "completed":
            if (
                record.producing_deployment_id is None
                or record.producing_task_policy_version is None
            ):
                raise GatewayFailure("invalid_worker_output", False, 500)
            try:
                content = self.store.output(record)
            except StoreError as exc:
                raise GatewayFailure("invalid_worker_output", False, 500) from exc
            return {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": record.request_id,
                "status": "completed",
                "output": {
                    "media_type": record.output_media_type,
                    "content": content,
                },
                "provenance": {
                    "task_policy_version": record.producing_task_policy_version,
                    "deployment_id": record.producing_deployment_id,
                },
            }
        if record.state == "failed":
            raise GatewayFailure(
                record.error_code or "invalid_worker_output",
                bool(record.error_retryable),
                502,
                record.error_retry_after_seconds,
            )
        if record.state == "expired":
            raise GatewayFailure("request_expired", False, 409)
        if record.state == "acknowledged":
            raise GatewayFailure("unknown_request", False, 410)
        return None

    def _claim_lane(self, request_id: str) -> tuple[_ActiveAttempt, bool]:
        with self._lane_lock:
            if self._active is None:
                self._active = _ActiveAttempt(request_id, threading.Event())
                return self._active, True
            return self._active, False

    def _release_lane(self, active: _ActiveAttempt) -> None:
        with self._lane_lock:
            active.finished.set()
            if self._active is active:
                self._active = None

    def _join_active(
        self, record: RequestRecord, credential: Credential, digest: str
    ) -> dict[str, object]:
        with self._lane_lock:
            active = self._active
        if active is None or active.request_id != record.request_id:
            return self._reload_after_attempt(record.request_id, credential, digest)
        return self._wait_and_reload(active, credential, digest, record.expires_at)

    def _wait_and_reload(
        self,
        active: _ActiveAttempt,
        credential: Credential,
        digest: str,
        expires_at: datetime,
    ) -> dict[str, object]:
        remaining = min(
            self.settings.worker_timeout_seconds + 1,
            max(0.001, (expires_at - self.clock()).total_seconds()),
        )
        if not active.finished.wait(remaining):
            raise GatewayFailure("inference_timeout", True, 504, self.settings.retry_after_seconds)
        return self._reload_after_attempt(active.request_id, credential, digest)

    def _reload_after_attempt(
        self,
        request_id: str,
        credential: Credential,
        digest: str,
    ) -> dict[str, object]:
        record = self.store.get_owned(request_id, credential.identity_hash, digest, self.clock())
        response = self._existing_response(record)
        if response is not None:
            return response
        if record.state == "reserved":
            raise GatewayFailure("worker_unavailable", True, 503, self.settings.retry_after_seconds)
        raise GatewayFailure("inference_timeout", True, 504, self.settings.retry_after_seconds)


def create_app(
    settings: Settings | None = None,
    *,
    credentials: CredentialStore | None = None,
    store: RequestStore | None = None,
    worker: InferenceWorker | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    credentials = credentials or CredentialStore.from_file(settings.credentials_path)
    store = store or RequestStore(
        settings.database_path,
        ResultCipher.from_file(settings.encryption_key_path),
        max_open_total=settings.max_open_total,
        max_open_per_credential=settings.max_open_per_credential,
        tombstone_retention_seconds=settings.tombstone_retention_seconds,
    )
    clock = clock or (lambda: datetime.now(UTC))
    store.initialize(clock())
    if worker is None:
        primary = OllamaWorker(settings.ollama_base_url, settings.ollama_model)
        fallback_settings = settings.lm_studio_fallback
        if fallback_settings is None:
            worker = primary
        else:
            fallback = LMStudioWorker(
                fallback_settings.base_url,
                fallback_settings.model,
                read_private_token(fallback_settings.token_path, "LM Studio"),
                fallback_settings.idle_ttl_seconds,
            )
            worker = FallbackWorker(primary, fallback, frozenset(TASK_POLICIES))
    service = GatewayService(settings, store, worker, clock)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        maintenance = asyncio.create_task(
            _run_maintenance(store, clock, settings.maintenance_interval_seconds)
        )
        try:
            yield
        finally:
            maintenance.cancel()
            with suppress(asyncio.CancelledError):
                await maintenance

    app = FastAPI(
        title="Local Inference Gateway",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.gateway_service = service

    @app.get("/health/live")
    def liveness() -> dict[str, object]:
        return {"protocol_version": PROTOCOL_VERSION, "status": "live"}

    @app.get("/v1/health")
    def health(request: Request) -> JSONResponse:
        credential = _authenticate(request, credentials)
        if credential is None:
            return _generic_auth_failure()
        return JSONResponse(service.health(credential))

    @app.post("/v1/inference")
    async def inference(request: Request) -> JSONResponse:
        credential = _authenticate(request, credentials)
        if credential is None:
            return _generic_auth_failure()
        request_id: str | None = None
        try:
            document = await _bounded_document(request, settings.request_max_bytes)
            request_id = safe_request_id(document.get("request_id"))
            parsed = InferenceRequest.model_validate(document)
            result = await run_in_threadpool(service.infer, credential, parsed)
            return JSONResponse(result)
        except GatewayFailure as failure:
            return _failure_response(request_id, failure)
        except (ValidationError, ValueError):
            return _failure_response(
                request_id,
                GatewayFailure("invalid_request", False, 422),
            )

    @app.post("/v1/inference/{path_request_id}/ack")
    async def acknowledge(path_request_id: str, request: Request) -> JSONResponse:
        credential = _authenticate(request, credentials)
        if credential is None:
            return _generic_auth_failure()
        request_id = safe_request_id(path_request_id)
        try:
            if request_id is None:
                raise GatewayFailure("invalid_request", False, 422)
            document = await _bounded_document(request, settings.request_max_bytes)
            parsed = AcknowledgementRequest.model_validate(document)
            if parsed.request_id != path_request_id:
                raise GatewayFailure("invalid_request", False, 422)
            result = await run_in_threadpool(service.acknowledge, credential, parsed)
            return JSONResponse(result)
        except GatewayFailure as failure:
            return _failure_response(request_id, failure)
        except (ValidationError, ValueError):
            return _failure_response(
                request_id,
                GatewayFailure("invalid_request", False, 422),
            )

    return app


async def _run_maintenance(
    store: RequestStore,
    clock: Callable[[], datetime],
    interval_seconds: float,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await run_in_threadpool(store.maintain, clock())
        except Exception:
            # Cleanup is retried on the next bounded interval. Request paths also
            # continue to run the same transactional cleanup before state changes.
            continue


def _authenticate(request: Request, credentials: CredentialStore) -> Credential | None:
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.casefold() != "bearer" or not token:
        return None
    return credentials.authenticate(token)


async def _bounded_document(request: Request, limit: int) -> dict[str, object]:
    content_encoding = request.headers.get("content-encoding", "identity").strip().casefold()
    if content_encoding not in {"", "identity"}:
        raise ValueError("encoded request bodies are not supported")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
    if content_type != "application/json":
        raise ValueError("request body must be application/json")
    body = bytearray()
    async for chunk in request.stream():
        if len(chunk) > limit - len(body):
            raise ValueError("request body exceeds its byte limit")
        body.extend(chunk)
    return parse_json_object(bytes(body))


def _generic_auth_failure() -> JSONResponse:
    return JSONResponse(
        {
            "protocol_version": PROTOCOL_VERSION,
            "status": "failed",
            "error": {"code": "unauthenticated", "retryable": False},
        },
        status_code=401,
    )


def _failure_response(request_id: str | None, failure: GatewayFailure) -> JSONResponse:
    body: dict[str, object] = {
        "protocol_version": PROTOCOL_VERSION,
        "status": "failed",
        "error": {"code": failure.code, "retryable": failure.retryable},
    }
    if request_id is not None:
        body["request_id"] = request_id
    if failure.retry_after_seconds is not None:
        error = body["error"]
        assert isinstance(error, dict)
        error["retry_after_seconds"] = failure.retry_after_seconds
    return JSONResponse(body, status_code=failure.status_code)
