from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from local_inference_gateway.contracts import InferenceRequest, parse_json_object
from local_inference_gateway.worker import (
    FallbackWorker,
    InvalidWorkerOutput,
    LMStudioWorker,
    OllamaWorker,
    WorkerOutcomeAmbiguous,
    WorkerResult,
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


class StubWorker:
    def __init__(
        self,
        *,
        available: bool,
        result: str,
        error: Exception | None = None,
    ) -> None:
        self.available = available
        self.result = result
        self.error = error
        self.health_calls: list[tuple[str, int] | None] = []
        self.infer_calls = 0

    def health(self, task: tuple[str, int] | None = None) -> bool:
        self.health_calls.append(task)
        return self.available

    def infer(self, request: InferenceRequest, timeout_seconds: float) -> WorkerResult:
        del request, timeout_seconds
        self.infer_calls += 1
        if self.error is not None:
            raise self.error
        return WorkerResult("application/json", self.result)


class StubPrimary(StubWorker):
    def __init__(self, *, available: bool, capacity_available: bool, error=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(available=available, result='{"worker":"primary"}', error=error)
        self.capacity_available = capacity_available
        self.capacity_calls = 0

    def fallback_capacity_available(self) -> bool:
        self.capacity_calls += 1
        return self.capacity_available


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
    assert "seed" not in requests[0]
    assert "model" not in gateway.request()
    assert requests[0]["response_format"]["json_schema"]["strict"] is True  # type: ignore[index]

    seeded = gateway.request()
    seeded["generation"]["seed"] = 9_223_372_036_854_775_807  # type: ignore[index]
    worker.infer(InferenceRequest.model_validate(seeded), 30)
    assert requests[1]["seed"] == 9_223_372_036_854_775_807


def test_worker_health_requires_exact_configured_model() -> None:
    worker = worker_with_handler(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=httpx.ByteStream(json.dumps({"data": [{"id": "different-model"}]}).encode()),
        )
    )
    assert worker.health() is False


@pytest.mark.parametrize("models", [[], [{"name": "resident-model"}], "invalid"])
def test_ollama_fallback_capacity_requires_exact_empty_residency(models: object) -> None:
    worker = worker_with_handler(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=httpx.ByteStream(json.dumps({"models": models}).encode()),
        )
    )

    assert worker.fallback_capacity_available() is (models == [])


def test_ollama_fallback_capacity_fails_closed_on_unknown_response() -> None:
    worker = worker_with_handler(
        lambda request: httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=httpx.ByteStream(b"not accepted"),
        )
    )

    assert worker.fallback_capacity_available() is False


def test_lm_studio_worker_authenticates_and_bounds_jit_residency(gateway) -> None:  # type: ignore[no-untyped-def]
    observed: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=httpx.ByteStream(
                json.dumps({"choices": [{"message": {"content": '{"ok":true}'}}]}).encode()
            ),
        )

    worker = LMStudioWorker(
        "http://127.0.0.1:1234",
        "pinned-fallback-model",
        "private-lm-studio-token-00000000",
        120,
    )
    production_client = worker._client(30)
    assert production_client.headers["authorization"] == ("Bearer private-lm-studio-token-00000000")
    asyncio.run(production_client.aclose())
    worker._client = lambda timeout: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=httpx.MockTransport(handler),
        timeout=timeout,
        trust_env=False,
        follow_redirects=False,
        headers={"Accept-Encoding": "identity"},
    )

    result = worker.infer(InferenceRequest.model_validate(gateway.request()), 30)

    assert result.content == '{"ok":true}'
    assert observed[0]["model"] == "pinned-fallback-model"
    assert observed[0]["ttl"] == 120
    assert observed[0]["stream"] is False
    assert observed[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "email_analyze_v1",
            "strict": True,
            "schema": gateway.request()["generation"]["response_schema"],  # type: ignore[index]
        },
    }


def test_fallback_worker_prefers_primary_without_touching_fallback(gateway) -> None:  # type: ignore[no-untyped-def]
    primary = StubPrimary(available=True, capacity_available=True)
    fallback = StubWorker(available=True, result='{"worker":"fallback"}')
    worker = FallbackWorker(
        primary,  # type: ignore[arg-type]
        fallback,
        frozenset({("email.analyze", 1)}),
    )
    request = InferenceRequest.model_validate(gateway.request())

    result = worker.infer(request, 30)

    assert result.content == '{"worker":"primary"}'
    assert primary.infer_calls == 1
    assert primary.capacity_calls == 0
    assert fallback.health_calls == []
    assert fallback.infer_calls == 0


def test_fallback_worker_uses_fallback_only_before_primary_submission(gateway) -> None:  # type: ignore[no-untyped-def]
    primary = StubPrimary(available=False, capacity_available=True)
    fallback = StubWorker(available=True, result='{"worker":"fallback"}')
    worker = FallbackWorker(
        primary,  # type: ignore[arg-type]
        fallback,
        frozenset({("email.analyze", 1)}),
    )
    request = InferenceRequest.model_validate(gateway.request())

    result = worker.infer(request, 30)

    assert result.content == '{"worker":"fallback"}'
    assert primary.infer_calls == 0
    assert primary.capacity_calls == 1
    assert fallback.health_calls == [("email.analyze", 1)]
    assert fallback.infer_calls == 1


@pytest.mark.parametrize(
    ("eligible", "capacity", "fallback_available"),
    [(False, True, True), (True, False, True), (True, True, False)],
)
def test_fallback_worker_fails_closed_before_dispatch(
    gateway,
    eligible: bool,
    capacity: bool,
    fallback_available: bool,  # type: ignore[no-untyped-def]
) -> None:
    primary = StubPrimary(available=False, capacity_available=capacity)
    fallback = StubWorker(available=fallback_available, result='{"worker":"fallback"}')
    worker = FallbackWorker(
        primary,  # type: ignore[arg-type]
        fallback,
        frozenset({("email.analyze", 1)}) if eligible else frozenset(),
    )

    with pytest.raises(WorkerUnavailable, match="safely available"):
        worker.infer(InferenceRequest.model_validate(gateway.request()), 30)

    assert primary.infer_calls == 0
    assert fallback.infer_calls == 0


@pytest.mark.parametrize(
    "error",
    [
        WorkerUnavailable("primary unavailable after selection"),
        WorkerOutcomeAmbiguous("primary outcome unknown"),
        InvalidWorkerOutput("primary output invalid"),
    ],
)
def test_fallback_worker_never_switches_after_primary_inference_begins(
    gateway,
    error: Exception,  # type: ignore[no-untyped-def]
) -> None:
    primary = StubPrimary(available=True, capacity_available=True, error=error)
    fallback = StubWorker(available=True, result='{"worker":"fallback"}')
    worker = FallbackWorker(
        primary,  # type: ignore[arg-type]
        fallback,
        frozenset({("email.analyze", 1)}),
    )

    with pytest.raises(type(error), match=str(error)):
        worker.infer(InferenceRequest.model_validate(gateway.request()), 30)

    assert primary.infer_calls == 1
    assert fallback.health_calls == []
    assert fallback.infer_calls == 0


@pytest.mark.parametrize("status", [404, 429, 500, 502, 503, 504, 599])
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


def test_worker_rejects_array_items_that_do_not_match_declared_schema(gateway) -> None:  # type: ignore[no-untyped-def]
    document = gateway.request()
    document["generation"]["response_schema"] = {  # type: ignore[index]
        "type": "object",
        "properties": {
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"quote": {"type": "string", "maxLength": 500}},
                    "required": ["quote"],
                    "additionalProperties": False,
                },
                "maxItems": 4,
            }
        },
        "required": ["evidence"],
        "additionalProperties": False,
    }
    body = json.dumps(
        {"choices": [{"message": {"content": '{"evidence":[{"wrong":true}]}'}}]}
    ).encode()
    worker = worker_with_handler(lambda request: httpx.Response(200, stream=httpx.ByteStream(body)))

    with pytest.raises(InvalidWorkerOutput, match="response schema"):
        worker.infer(InferenceRequest.model_validate(document), 30)


def test_worker_enforces_bounded_root_object_choice(gateway) -> None:  # type: ignore[no-untyped-def]
    document = gateway.request()
    document["generation"]["response_schema"] = {  # type: ignore[index]
        "anyOf": [
            {
                "type": "object",
                "properties": {"outcome": {"type": "string", "enum": ["empty"]}},
                "required": ["outcome"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {"summary": {"type": "string", "minLength": 1}},
                "required": ["summary"],
                "additionalProperties": False,
            },
        ]
    }
    invalid_body = json.dumps(
        {"choices": [{"message": {"content": '{"unbounded":"value"}'}}]}
    ).encode()
    worker = worker_with_handler(
        lambda request: httpx.Response(200, stream=httpx.ByteStream(invalid_body))
    )

    with pytest.raises(InvalidWorkerOutput, match="response schema"):
        worker.infer(InferenceRequest.model_validate(document), 30)


def test_worker_validates_generated_numbers_without_binary_float_rounding(gateway) -> None:  # type: ignore[no-untyped-def]
    document = gateway.request()
    document["generation"]["response_schema"] = {  # type: ignore[index]
        "type": "object",
        "properties": {"x": {"type": "number", "maximum": 0}},
        "required": ["x"],
    }
    body = b'{"choices":[{"message":{"content":"{\\"x\\":1e-999}"}}]}'
    worker = worker_with_handler(lambda request: httpx.Response(200, stream=httpx.ByteStream(body)))

    with pytest.raises(InvalidWorkerOutput, match="response schema"):
        worker.infer(InferenceRequest.model_validate(document), 30)


def test_worker_matches_fractional_schema_enums_in_the_exact_numeric_domain(gateway) -> None:  # type: ignore[no-untyped-def]
    document = gateway.request()
    document["generation"]["response_schema"] = {  # type: ignore[index]
        "type": "object",
        "properties": {"score": {"type": "number", "enum": [0.1]}},
        "required": ["score"],
    }
    body = b'{"choices":[{"message":{"content":"{\\"score\\":0.1}"}}]}'
    worker = worker_with_handler(lambda request: httpx.Response(200, stream=httpx.ByteStream(body)))

    assert worker.infer(InferenceRequest.model_validate(document), 30).content == '{"score":0.1}'


def test_worker_preserves_exact_schema_decimals_in_dispatch_and_validation(gateway) -> None:  # type: ignore[no-untyped-def]
    raw = json.dumps(gateway.request()).replace(
        '"response_schema": {"type": "object"}',
        '"response_schema": {"type": "object", "properties": '
        '{"score": {"type": "number", "enum": [1e-999]}}, "required": ["score"]}',
    )
    request = InferenceRequest.model_validate(parse_json_object(raw.encode()))
    dispatched: list[bytes] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        dispatched.append(http_request.content)
        return httpx.Response(
            200,
            stream=httpx.ByteStream(
                b'{"choices":[{"message":{"content":"{\\"score\\":1e-999}"}}]}'
            ),
        )

    worker = worker_with_handler(handler)

    assert worker.infer(request, 30).content == '{"score":1e-999}'
    assert b'"enum":[1e-999]' in dispatched[0]


@pytest.mark.parametrize(
    "body",
    [
        b'{"choices":[{"message":{"content":"{\\"value\\":' + b"9" * 400 + b'}"}}]}',
        b'{"oversized":' + b"9" * 400 + b',"choices":[{"message":{"content":"{}"}}]}',
    ],
)
def test_worker_rejects_integer_tokens_outside_the_supported_finite_range(
    gateway,
    body: bytes,  # type: ignore[no-untyped-def]
) -> None:
    worker = worker_with_handler(lambda request: httpx.Response(200, stream=httpx.ByteStream(body)))

    with pytest.raises(InvalidWorkerOutput, match="valid JSON"):
        worker.infer(InferenceRequest.model_validate(gateway.request()), 30)


def test_worker_rejects_duplicate_generated_keys(gateway) -> None:  # type: ignore[no-untyped-def]
    body = b'{"choices":[{"message":{"content":"{\\"ok\\":false,\\"ok\\":true}"}}]}'
    worker = worker_with_handler(lambda request: httpx.Response(200, stream=httpx.ByteStream(body)))

    with pytest.raises(InvalidWorkerOutput, match="valid JSON"):
        worker.infer(InferenceRequest.model_validate(gateway.request()), 30)


def test_worker_rejects_token_limited_completion(gateway) -> None:  # type: ignore[no-untyped-def]
    body = json.dumps(
        {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": '{"summary":"valid prefix"}'},
                }
            ]
        }
    ).encode()
    worker = worker_with_handler(lambda request: httpx.Response(200, stream=httpx.ByteStream(body)))

    with pytest.raises(InvalidWorkerOutput, match="finish normally"):
        worker.infer(InferenceRequest.model_validate(gateway.request()), 30)


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
