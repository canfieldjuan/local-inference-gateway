from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from local_inference_gateway.contracts import InferenceRequest
from local_inference_gateway.worker import (
    InvalidWorkerOutput,
    OllamaWorker,
    WorkerOutcomeAmbiguous,
    WorkerUnavailable,
)


def worker_with_handler(handler) -> OllamaWorker:  # type: ignore[no-untyped-def]
    worker = OllamaWorker("http://127.0.0.1:11434", "pinned-model")
    worker._client = lambda timeout: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=httpx.MockTransport(handler),
        timeout=timeout,
        trust_env=False,
        follow_redirects=False,
        headers={"Accept-Encoding": "identity"},
    )
    return worker


def test_worker_inserts_model_only_at_private_worker_boundary(gateway) -> None:  # type: ignore[no-untyped-def]
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=httpx.ByteStream(
                json.dumps({"choices": [{"message": {"content": '{"ok":true}'}}]}).encode()
            ),
        )

    worker = worker_with_handler(handler)
    result = worker.infer(InferenceRequest.model_validate(gateway.request()), 30)

    assert result.content == '{"ok":true}'
    assert requests[0]["model"] == "pinned-model"
    assert "model" not in gateway.request()
    assert requests[0]["response_format"]["json_schema"]["strict"] is True  # type: ignore[index]


def test_worker_health_requires_exact_configured_model() -> None:
    worker = worker_with_handler(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=httpx.ByteStream(json.dumps({"data": [{"id": "different-model"}]}).encode()),
        )
    )
    assert worker.health() is False


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_worker_definitive_unavailable_response_is_retryable(gateway, status: int) -> None:  # type: ignore[no-untyped-def]
    worker = worker_with_handler(lambda request: httpx.Response(status))

    with pytest.raises(WorkerUnavailable):
        worker.infer(InferenceRequest.model_validate(gateway.request()), 30)


def test_worker_read_failure_is_ambiguous(gateway) -> None:  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("lost response", request=request)

    with pytest.raises(WorkerOutcomeAmbiguous):
        worker_with_handler(handler).infer(InferenceRequest.model_validate(gateway.request()), 30)


def test_worker_rejects_encoded_or_oversized_response(gateway) -> None:  # type: ignore[no-untyped-def]
    encoded = worker_with_handler(
        lambda request: httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=httpx.ByteStream(b"not accepted"),
        )
    )
    oversized = worker_with_handler(
        lambda request: httpx.Response(200, content=b"{" + b"x" * 1_000_001)
    )

    with pytest.raises(InvalidWorkerOutput):
        encoded.infer(InferenceRequest.model_validate(gateway.request()), 30)
    with pytest.raises(InvalidWorkerOutput):
        oversized.infer(InferenceRequest.model_validate(gateway.request()), 30)


@pytest.mark.parametrize("content", ["not JSON", "[]"])
def test_worker_rejects_non_object_json_content(gateway, content: str) -> None:  # type: ignore[no-untyped-def]
    body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
    worker = worker_with_handler(lambda request: httpx.Response(200, stream=httpx.ByteStream(body)))

    with pytest.raises(InvalidWorkerOutput):
        worker.infer(InferenceRequest.model_validate(gateway.request()), 30)


@pytest.mark.parametrize("content", ['{"value":NaN}', '{"value":1e999}'])
def test_worker_rejects_non_finite_json_content(gateway, content: str) -> None:  # type: ignore[no-untyped-def]
    body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
    worker = worker_with_handler(lambda request: httpx.Response(200, stream=httpx.ByteStream(body)))

    with pytest.raises(InvalidWorkerOutput, match="valid JSON"):
        worker.infer(InferenceRequest.model_validate(gateway.request()), 30)


def test_worker_rejects_outer_json_number_that_overflows_to_infinity(gateway) -> None:  # type: ignore[no-untyped-def]
    body = b'{"overflow":1e999,"choices":[{"message":{"content":"{}"}}]}'
    worker = worker_with_handler(lambda request: httpx.Response(200, stream=httpx.ByteStream(body)))

    with pytest.raises(InvalidWorkerOutput, match="valid JSON"):
        worker.infer(InferenceRequest.model_validate(gateway.request()), 30)


def test_worker_rejects_json_that_does_not_match_declared_schema(gateway) -> None:  # type: ignore[no-untyped-def]
    document = gateway.request()
    document["generation"]["response_schema"] = {  # type: ignore[index]
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": False,
    }
    body = json.dumps({"choices": [{"message": {"content": '{"wrong":true}'}}]}).encode()
    worker = worker_with_handler(lambda request: httpx.Response(200, stream=httpx.ByteStream(body)))

    with pytest.raises(InvalidWorkerOutput, match="response schema"):
        worker.infer(InferenceRequest.model_validate(document), 30)


class _PeriodicNeverEndingStream(httpx.AsyncByteStream):
    async def __aiter__(self):  # type: ignore[no-untyped-def]
        while True:
            await asyncio.sleep(0.005)
            yield b" "


def test_worker_enforces_absolute_deadline_while_bytes_arrive(gateway) -> None:  # type: ignore[no-untyped-def]
    worker = worker_with_handler(
        lambda request: httpx.Response(200, stream=_PeriodicNeverEndingStream())
    )
    started = time.monotonic()

    with pytest.raises(WorkerOutcomeAmbiguous):
        worker.infer(InferenceRequest.model_validate(gateway.request()), 0.03)

    assert time.monotonic() - started < 1.0
