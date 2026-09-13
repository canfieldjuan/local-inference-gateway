from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from .contracts import (
    InferenceRequest,
    encode_json_bytes,
    normalize_json_numbers,
    parse_exact_json_decimal,
    parse_json_integer,
    parse_json_object_pairs,
)

MAX_WORKER_RESPONSE_BYTES = 1_000_000
MAX_OUTPUT_CONTENT_BYTES = 750_000


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


class InferenceWorker(Protocol):
    def health(self, task: tuple[str, int] | None = None) -> bool: ...

    def infer(self, request: InferenceRequest, timeout_seconds: float) -> WorkerResult: ...


class OllamaWorker:
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url
        self.model = model

    def health(self, task: tuple[str, int] | None = None) -> bool:
        del task
        try:
            return asyncio.run(self._health())
        except (TimeoutError, httpx.HTTPError, InvalidWorkerOutput):
            return False

    async def _health(self) -> bool:
        async with asyncio.timeout(5.0):
            async with (
                self._client(5.0) as client,
                client.stream("GET", f"{self.base_url}/v1/models") as response,
            ):
                if response.status_code != 200:
                    return False
                document = await _bounded_json(response)
            models = document.get("data")
            return isinstance(models, list) and any(
                isinstance(item, dict) and item.get("id") == self.model for item in models
            )

    def infer(self, request: InferenceRequest, timeout_seconds: float) -> WorkerResult:
        return asyncio.run(self._infer(request, timeout_seconds))

    def fallback_capacity_available(self) -> bool:
        try:
            return asyncio.run(self._fallback_capacity_available())
        except (TimeoutError, httpx.HTTPError, InvalidWorkerOutput):
            return False

    async def _fallback_capacity_available(self) -> bool:
        async with asyncio.timeout(5.0):
            async with (
                self._client(5.0) as client,
                client.stream("GET", f"{self.base_url}/api/ps") as response,
            ):
                if response.status_code != 200:
                    return False
                document = await _bounded_json(response)
        return document.get("models") == []

    async def _infer(self, request: InferenceRequest, timeout_seconds: float) -> WorkerResult:
        payload = self._payload(request)
        try:
            async with asyncio.timeout(timeout_seconds):
                async with (
                    self._client(timeout_seconds) as client,
                    client.stream(
                        "POST",
                        f"{self.base_url}/v1/chat/completions",
                        content=encode_json_bytes(payload),
                        headers={"Content-Type": "application/json"},
                    ) as response,
                ):
                    if response.status_code in {404, 429} or 500 <= response.status_code <= 599:
                        raise WorkerUnavailable("worker rejected admission while unavailable")
                    if response.status_code >= 400:
                        raise InvalidWorkerOutput("worker rejected the gateway request")
                    document = await _bounded_json(response)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise WorkerUnavailable("worker connection was unavailable") from exc
        except (WorkerUnavailable, InvalidWorkerOutput):
            raise
        except (TimeoutError, httpx.HTTPError) as exc:
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
                    "schema": generation.response_schema,
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

    def fallback_capacity_available(self) -> bool:
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

    def health(self, task: tuple[str, int] | None = None) -> bool:
        if self.primary.health(task):
            return True
        return self._fallback_ready(task)

    def infer(self, request: InferenceRequest, timeout_seconds: float) -> WorkerResult:
        task = (request.task.id, request.task.version)
        if self.primary.health(task):
            return self.primary.infer(request, timeout_seconds)
        if not self._fallback_ready(task):
            raise WorkerUnavailable("no worker is safely available before dispatch")
        return self.fallback.infer(request, timeout_seconds)

    def _fallback_ready(self, task: tuple[str, int] | None) -> bool:
        return (
            task is not None
            and task in self.eligible_tasks
            and self.primary.fallback_capacity_available()
            and self.fallback.health(task)
        )


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
