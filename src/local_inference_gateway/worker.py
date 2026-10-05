from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from .contracts import (
    InferenceRequest,
    decoder_schema,
    encode_json_bytes,
    normalize_json_numbers,
    parse_exact_json_decimal,
    parse_json_integer,
    parse_json_object_pairs,
    required_context_tokens,
    uses_passage_definitions,
)

MAX_WORKER_RESPONSE_BYTES = 1_000_000
MAX_OUTPUT_CONTENT_BYTES = 750_000
MAX_WORKER_CONTEXT_TOKENS = 1_048_576


class WorkerError(RuntimeError):
    """An inference worker did not produce an accepted result."""


class WorkerUnavailable(WorkerError):
    """The worker definitively did not admit this request."""


class WorkerOutcomeAmbiguous(WorkerError):
    """The gateway cannot prove whether the worker accepted the request."""


class InvalidWorkerOutput(WorkerError):
    """The worker response violated the gateway envelope."""


@dataclass(frozen=True)
class WorkerResult:
    media_type: str
    content: str


class WorkerAvailability(Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class InferenceWorker(Protocol):
    def health(
        self,
        task: tuple[str, int] | None = None,
        timeout_seconds: float = 5.0,
    ) -> bool: ...

    def infer(
        self,
        request: InferenceRequest,
        timeout_seconds: float,
        *,
        deadline: float | None = None,
    ) -> WorkerResult: ...


class OllamaWorker:
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url
        self.model = model

    def health(
        self,
        task: tuple[str, int] | None = None,
        timeout_seconds: float = 5.0,
    ) -> bool:
        return self.availability(task, timeout_seconds) is WorkerAvailability.AVAILABLE

    def availability(
        self,
        task: tuple[str, int] | None = None,
        timeout_seconds: float = 5.0,
    ) -> WorkerAvailability:
        try:
            return asyncio.run(self._availability(timeout_seconds, required_context_tokens(task)))
        except (TimeoutError, httpx.HTTPError, InvalidWorkerOutput):
            return WorkerAvailability.UNKNOWN

    async def _availability(
        self, timeout_seconds: float, minimum_context: int
    ) -> WorkerAvailability:
        async with asyncio.timeout(timeout_seconds):
            async with self._client(timeout_seconds) as client:
                async with client.stream("GET", f"{self.base_url}/v1/models") as response:
                    if response.status_code != 200:
                        return WorkerAvailability.UNKNOWN
                    document = await _bounded_json(response)
                models = document.get("data")
                if not isinstance(models, list):
                    return WorkerAvailability.UNKNOWN
                model_ids: list[str] = []
                for item in models:
                    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                        return WorkerAvailability.UNKNOWN
                    model_ids.append(item["id"])
                if self.model not in model_ids:
                    return WorkerAvailability.UNAVAILABLE
                return await self._context_availability(client, minimum_context)

    async def _context_availability(
        self, client: httpx.AsyncClient, minimum: int
    ) -> WorkerAvailability:
        if minimum == 0:
            return WorkerAvailability.AVAILABLE
        async with client.stream(
            "POST", f"{self.base_url}/api/show", json={"model": self.model}
        ) as response:
            if response.status_code != 200:
                return WorkerAvailability.UNKNOWN
            configuration = await _bounded_json(response)
        parameters = configuration.get("parameters")
        if not isinstance(parameters, str):
            return WorkerAvailability.UNKNOWN
        settings = [line.split() for line in parameters.splitlines() if line.strip()]
        contexts = [fields for fields in settings if fields[0] == "num_ctx"]
        if (
            len(contexts) != 1
            or len(contexts[0]) != 2
            or not contexts[0][1].isascii()
            or not contexts[0][1].isdigit()
            or len(contexts[0][1]) > 7
        ):
            return WorkerAvailability.UNKNOWN
        configured = int(contexts[0][1])
        if not 1 <= configured <= MAX_WORKER_CONTEXT_TOKENS:
            return WorkerAvailability.UNKNOWN
        if configured < minimum:
            return WorkerAvailability.UNAVAILABLE
        async with client.stream("GET", f"{self.base_url}/api/ps") as response:
            if response.status_code != 200:
                return WorkerAvailability.UNKNOWN
            resident = await _bounded_json(response)
        models = resident.get("models")
        if not isinstance(models, list):
            return WorkerAvailability.UNKNOWN
        matched = False
        for item in models:
            if not isinstance(item, dict):
                return WorkerAvailability.UNKNOWN
            identities = [item[key] for key in ("name", "model") if key in item]
            if not identities or any(not isinstance(v, str) or not v for v in identities):
                return WorkerAvailability.UNKNOWN
            if self.model not in identities:
                continue
            if matched or any(value != self.model for value in identities):
                return WorkerAvailability.UNKNOWN
            matched = True
            context = item.get("context_length")
            if type(context) is not int or not 1 <= context <= MAX_WORKER_CONTEXT_TOKENS:
                return WorkerAvailability.UNKNOWN
            if context < minimum:
                return WorkerAvailability.UNAVAILABLE
        return WorkerAvailability.AVAILABLE

    def infer(
        self,
        request: InferenceRequest,
        timeout_seconds: float,
        *,
        deadline: float | None = None,
    ) -> WorkerResult:
        return asyncio.run(self._infer(request, timeout_seconds, deadline))

    def fallback_capacity_available(self, timeout_seconds: float = 5.0) -> bool:
        try:
            return asyncio.run(self._fallback_capacity_available(timeout_seconds))
        except (TimeoutError, httpx.HTTPError, InvalidWorkerOutput):
            return False

    async def _fallback_capacity_available(self, timeout_seconds: float) -> bool:
        async with asyncio.timeout(timeout_seconds):
            async with (
                self._client(timeout_seconds) as client,
                client.stream("GET", f"{self.base_url}/api/ps") as response,
            ):
                if response.status_code != 200:
                    return False
                document = await _bounded_json(response)
        return document.get("models") == []

    async def _infer(
        self,
        request: InferenceRequest,
        timeout_seconds: float,
        deadline: float | None,
    ) -> WorkerResult:
        payload = self._payload(request)
        submitted = False
        try:
            async with self._client(timeout_seconds) as client:
                dispatch_timeout = _dispatch_timeout(timeout_seconds, deadline)
                async with asyncio.timeout(dispatch_timeout):
                    capacity = await self._context_availability(
                        client,
                        required_context_tokens((request.task.id, request.task.version)),
                    )
                    if capacity is not WorkerAvailability.AVAILABLE:
                        raise WorkerUnavailable("worker task context capacity is not established")
                    submitted = True
                    async with client.stream(
                        "POST",
                        f"{self.base_url}/v1/chat/completions",
                        content=encode_json_bytes(
                            payload,
                            sort_keys=not uses_passage_definitions(
                                request.generation.response_schema
                            ),
                        ),
                        headers={"Content-Type": "application/json"},
                    ) as response:
                        if response.status_code in {404, 429} or 500 <= response.status_code <= 599:
                            raise WorkerUnavailable("worker rejected admission while unavailable")
                        if response.status_code >= 400:
                            raise InvalidWorkerOutput("worker rejected the gateway request")
                        document = await _bounded_json(response)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise WorkerUnavailable("worker connection was unavailable") from exc
        except InvalidWorkerOutput as exc:
            if not submitted:
                raise WorkerUnavailable("worker capacity check failed before dispatch") from exc
            raise
        except WorkerUnavailable:
            raise
        except (TimeoutError, httpx.HTTPError) as exc:
            if not submitted:
                raise WorkerUnavailable("worker capacity check failed before dispatch") from exc
            raise WorkerOutcomeAmbiguous("worker outcome is unknown") from exc
        return _validated_result(request, document)

    def _payload(self, request: InferenceRequest) -> dict[str, object]:
        generation = request.generation
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [message.model_dump(mode="json") for message in generation.messages],
            "temperature": generation.temperature,
            "max_tokens": request.requirements.max_output_tokens,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.task.id.replace(".", "_") + f"_v{request.task.version}",
                    "strict": True,
                    "schema": decoder_schema(generation.response_schema),
                },
            },
        }
        if generation.seed is not None:
            payload["seed"] = generation.seed
        return payload

    def _client(self, timeout_seconds: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout_seconds,
            trust_env=False,
            follow_redirects=False,
            headers={"Accept-Encoding": "identity"},
        )


class LMStudioWorker(OllamaWorker):
    def __init__(self, base_url: str, model: str, token: str, idle_ttl_seconds: int):
        super().__init__(base_url, model)
        self._token = token
        self._idle_ttl_seconds = idle_ttl_seconds

    async def _context_availability(
        self, client: httpx.AsyncClient, minimum: int
    ) -> WorkerAvailability:
        # This worker has no verified capacity contract for the extended document task.
        return WorkerAvailability.UNAVAILABLE if minimum else WorkerAvailability.AVAILABLE

    def fallback_capacity_available(self, timeout_seconds: float = 5.0) -> bool:
        del timeout_seconds
        return False

    def _payload(self, request: InferenceRequest) -> dict[str, object]:
        payload = super()._payload(request)
        payload["ttl"] = self._idle_ttl_seconds
        return payload

    def _client(self, timeout_seconds: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout_seconds,
            trust_env=False,
            follow_redirects=False,
            headers={
                "Accept-Encoding": "identity",
                "Authorization": f"Bearer {self._token}",
            },
        )


class FallbackWorker:
    def __init__(
        self,
        primary: OllamaWorker,
        fallback: InferenceWorker,
        eligible_tasks: frozenset[tuple[str, int]],
    ):
        self.primary = primary
        self.fallback = fallback
        self.eligible_tasks = eligible_tasks

    def health(
        self,
        task: tuple[str, int] | None = None,
        timeout_seconds: float = 15.0,
    ) -> bool:
        try:
            deadline = time.monotonic() + timeout_seconds
            availability = self.primary.availability(task, self._remaining(deadline))
            if availability is WorkerAvailability.AVAILABLE:
                return True
            return availability is WorkerAvailability.UNAVAILABLE and self._fallback_ready(
                task, deadline
            )
        except WorkerUnavailable:
            return False

    def infer(
        self,
        request: InferenceRequest,
        timeout_seconds: float,
        *,
        deadline: float | None = None,
    ) -> WorkerResult:
        local_deadline = time.monotonic() + timeout_seconds
        deadline = local_deadline if deadline is None else min(deadline, local_deadline)
        task = (request.task.id, request.task.version)
        availability = self.primary.availability(task, self._remaining(deadline))
        if availability is WorkerAvailability.AVAILABLE:
            return self.primary.infer(
                request,
                self._remaining(deadline),
                deadline=deadline,
            )
        if availability is not WorkerAvailability.UNAVAILABLE or not self._fallback_ready(
            task, deadline
        ):
            raise WorkerUnavailable("no worker is safely available before dispatch")
        return self.fallback.infer(
            request,
            self._remaining(deadline),
            deadline=deadline,
        )

    def _fallback_ready(self, task: tuple[str, int] | None, deadline: float) -> bool:
        if task is None or task not in self.eligible_tasks:
            return False
        if not self.primary.fallback_capacity_available(self._remaining(deadline)):
            return False
        return self.fallback.health(task, self._remaining(deadline))

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WorkerUnavailable("worker routing deadline expired before dispatch")
        return remaining


def _validated_result(request: InferenceRequest, document: dict[str, object]) -> WorkerResult:
    generation = request.generation
    choices = document.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise InvalidWorkerOutput("worker response has no completion message")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise InvalidWorkerOutput("worker response has no completion message")
    if choices[0].get("finish_reason") not in {None, "stop"}:
        raise InvalidWorkerOutput("worker completion did not finish normally")
    content = message.get("content") or ""
    if not isinstance(content, str) or not content.strip():
        content = message.get("reasoning_content") or message.get("reasoning") or ""
    if not isinstance(content, str) or not content.strip():
        raise InvalidWorkerOutput("worker response has no generated content")
    try:
        encoded = content.encode("utf-8")
    except UnicodeError as exc:
        raise InvalidWorkerOutput("worker content is not valid UTF-8") from exc
    if len(encoded) > MAX_OUTPUT_CONTENT_BYTES:
        raise InvalidWorkerOutput("worker content exceeds its byte limit")
    try:
        generated = json.loads(
            encoded,
            parse_constant=_reject_json_constant,
            parse_float=parse_exact_json_decimal,
            parse_int=parse_json_integer,
            object_pairs_hook=parse_json_object_pairs,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise InvalidWorkerOutput("worker content is not valid JSON") from exc
    if not isinstance(generated, dict):
        raise InvalidWorkerOutput("worker content must be a JSON object")
    try:
        Draft202012Validator(normalize_json_numbers(generation.response_schema)).validate(generated)
    except (SchemaError, ValidationError) as exc:
        raise InvalidWorkerOutput("worker content does not match response schema") from exc
    return WorkerResult(media_type="application/json", content=content)


def _dispatch_timeout(timeout_seconds: float, deadline: float | None) -> float:
    if deadline is None:
        return timeout_seconds
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise WorkerUnavailable("worker routing deadline expired before dispatch")
    return min(timeout_seconds, remaining)


async def _bounded_json(response: httpx.Response) -> dict[str, object]:
    encoding = response.headers.get("content-encoding", "identity").strip().casefold()
    content_length = response.headers.get("content-length")
    if encoding not in {"", "identity"}:
        raise InvalidWorkerOutput("worker response encoding or size is unsupported")
    if content_length is not None:
        try:
            if int(content_length) > MAX_WORKER_RESPONSE_BYTES:
                raise InvalidWorkerOutput("worker response encoding or size is unsupported")
        except ValueError as exc:
            raise InvalidWorkerOutput("worker response content length is invalid") from exc
    body = bytearray()
    async for chunk in response.aiter_raw():
        if len(body) + len(chunk) > MAX_WORKER_RESPONSE_BYTES:
            raise InvalidWorkerOutput("worker response encoding or size is unsupported")
        body.extend(chunk)
    try:
        document = json.loads(
            body,
            parse_constant=_reject_json_constant,
            parse_float=parse_exact_json_decimal,
            parse_int=parse_json_integer,
            object_pairs_hook=parse_json_object_pairs,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise InvalidWorkerOutput("worker response is not valid JSON") from exc
    if not isinstance(document, dict):
        raise InvalidWorkerOutput("worker response must be a JSON object")
    return document


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value} is not supported")
