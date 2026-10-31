from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import httpx
import pytest

from local_inference_gateway.contracts import InferenceRequest
from local_inference_gateway.worker import LMStudioWorker, OllamaWorker


def ordered_email_schema() -> dict[str, Any]:
    leaf = {
        "type": "object",
        "properties": {"a_tail": {"type": "boolean"}, "z_first": {"type": "boolean"}},
        "required": ["z_first", "a_tail"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "m_optional": {"type": "boolean"},
            "a_tail": {"anyOf": [deepcopy(leaf), {"type": "null"}]},
            "z_first": {"type": "array", "items": leaf, "maxItems": 1},
        },
        "required": ["z_first", "a_tail"],
        "additionalProperties": False,
    }


def wire_bytes(request: InferenceRequest, *, fallback: bool = False) -> bytes:
    captured: list[bytes] = []

    def handler(outgoing: httpx.Request) -> httpx.Response:
        assert outgoing.url.path == "/v1/chat/completions"
        captured.append(outgoing.content)
        return httpx.Response(
            200,
            stream=httpx.ByteStream(
                json.dumps(
                    {
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": '{"z_first":[],"a_tail":null}'},
                            }
                        ]
                    }
                ).encode()
            ),
        )

    worker = (
        LMStudioWorker("http://127.0.0.1:1234", "test-model", "synthetic-test-token", 30)
        if fallback
        else OllamaWorker("http://127.0.0.1:11434", "test-model")
    )
    worker._client = lambda timeout: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=httpx.MockTransport(handler), timeout=timeout, trust_env=False
    )
    worker.infer(request, 30)
    assert len(captured) == 1
    return captured[0]


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("sorted_input", [False, True])
def test_email_worker_wire_follows_required_order(
    gateway: Any, fallback: bool, sorted_input: bool
) -> None:
    document = gateway.request()
    schema = ordered_email_schema()
    document["generation"]["response_schema"] = schema
    if sorted_input:
        document = json.loads(json.dumps(document, sort_keys=True))
    admitted = InferenceRequest.model_validate(document)
    before = json.dumps(admitted.generation.response_schema)
    outgoing = json.loads(wire_bytes(admitted, fallback=fallback))
    sent = outgoing["response_format"]["json_schema"]["schema"]
    assert list(sent["properties"]) == ["z_first", "a_tail", "m_optional"]
    assert list(sent["properties"]["z_first"]["items"]["properties"]) == ["z_first", "a_tail"]
    assert list(sent["properties"]["a_tail"]["anyOf"][0]["properties"]) == ["z_first", "a_tail"]
    assert sent == schema
    assert outgoing["messages"] == document["generation"]["messages"]
    assert outgoing["temperature"] == 0.1
    assert outgoing["max_tokens"] == 500
    assert "seed" not in outgoing
    assert json.dumps(admitted.generation.response_schema) == before


def test_email_decoder_order_preserves_canonical_replay_identity(gateway: Any) -> None:
    document = gateway.request()
    document["generation"]["response_schema"] = ordered_email_schema()
    first = InferenceRequest.model_validate(document)
    reordered = InferenceRequest.model_validate(json.loads(json.dumps(document, sort_keys=True)))
    assert first.canonical_digest() == reordered.canonical_digest()
    assert wire_bytes(first) == wire_bytes(reordered)
    changed = deepcopy(document)
    changed["generation"]["response_schema"]["required"].reverse()
    other = InferenceRequest.model_validate(changed)
    assert first.canonical_digest() != other.canonical_digest()
    first_schema = json.loads(wire_bytes(first))["response_format"]["json_schema"]["schema"]
    other_schema = json.loads(wire_bytes(other))["response_format"]["json_schema"]["schema"]
    assert list(first_schema["properties"]) != list(other_schema["properties"])


@pytest.mark.parametrize(
    ("required", "expected"),
    [
        (None, ["a_tail", "m_optional", "z_first"]),
        ([], ["a_tail", "m_optional", "z_first"]),
        (["a_tail"], ["a_tail", "m_optional", "z_first"]),
        (["z_first", "a_tail"], ["z_first", "a_tail", "m_optional"]),
    ],
)
def test_email_optional_fields_have_stable_order(
    gateway: Any, required: list[str] | None, expected: list[str]
) -> None:
    document = gateway.request()
    schema = ordered_email_schema()
    if required is None:
        del schema["required"]
    else:
        schema["required"] = required
    document["generation"]["response_schema"] = schema
    sent = json.loads(wire_bytes(InferenceRequest.model_validate(document)))
    assert list(sent["response_format"]["json_schema"]["schema"]["properties"]) == expected


@pytest.mark.parametrize("task_id", ["email.schedule.extract", "invoice.extract.batch"])
def test_sibling_task_transport_stays_sorted(gateway: Any, task_id: str) -> None:
    document = gateway.request(task={"id": task_id, "version": 1})
    document["generation"]["response_schema"] = ordered_email_schema()
    outgoing = wire_bytes(InferenceRequest.model_validate(document))
    sent = json.loads(outgoing)
    assert list(sent["response_format"]["json_schema"]["schema"]["properties"]) == [
        "a_tail",
        "m_optional",
        "z_first",
    ]
    # Exact legacy encoder bytes, including all nested object keys and numbers.
    from local_inference_gateway.contracts import encode_json_bytes

    assert outgoing == encode_json_bytes(sent)
