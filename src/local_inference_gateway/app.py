from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from .config import Credential, CredentialStore, Settings
from .contracts import (
    PROTOCOL_VERSION,
    AcknowledgementRequest,
    InferenceRequest,
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
    InferenceWorker,
    InvalidWorkerOutput,
    OllamaWorker,
    WorkerOutcomeAmbiguous,
    WorkerUnavailable,
)

SUPPORTED_TASK = ("email.analyze", 1)


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
        tasks: list[dict[str, object]] = []
        if SUPPORTED_TASK in credential.tasks:
            available = self.worker.health()
            tasks.append(
                {
                    "id": SUPPORTED_TASK[0],
                    "version": SUPPORTED_TASK[1],
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
            timeout = min(
                self.settings.worker_timeout_seconds,
                max(0.001, (request.expires_at - current).total_seconds()),
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
                self.clock(),
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
        if task != SUPPORTED_TASK:
            raise GatewayFailure("unsupported_task", False, 422)
        lifetime = (request.expires_at - now).total_seconds()
        if lifetime <= 0:
            raise GatewayFailure("request_expired", False, 409)
        if lifetime > self.settings.request_max_lifetime_seconds:
            raise GatewayFailure("invalid_request", False, 422)
        if request.generation.temperature != 0.1:
            raise GatewayFailure("unsupported_task", False, 422)

    def _existing_response(self, record: RequestRecord) -> dict[str, object] | None:
        if record.state == "completed":
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
                    "task_policy_version": 1,
                    "deployment_id": self.settings.deployment_id,
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
            raise GatewayFailure("inference_timeout", True, 504, self.settings.retry_after_seconds)
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
        record = self.store.get_owned(
            active.request_id, credential.identity_hash, digest, self.clock()
        )
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
    worker = worker or OllamaWorker(settings.ollama_base_url, settings.ollama_model)
    service = GatewayService(settings, store, worker, clock)
    app = FastAPI(
        title="Local Inference Gateway",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
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
