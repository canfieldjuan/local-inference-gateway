from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
import pytest

from local_inference_gateway.contracts import InferenceRequest, encode_json_bytes
from local_inference_gateway.worker import LMStudioWorker, OllamaWorker

DIRECT_ORDER = [
    "category",
    "priority",
    "summary",
    "action_required",
    "suggested_action",
    "deadline_text",
    "deadline_iso",
    "confidence",
]
SORTED_ORDER = [
    "action_required",
    "category",
    "confidence",
    "deadline_iso",
    "deadline_text",
    "priority",
    "suggested_action",
    "summary",
]
EMAIL_OUTPUT = {
    "category": "other",
    "priority": "normal",
    "summary": "Synthetic transport fixture.",
    "action_required": False,
    "suggested_action": None,
    "deadline_text": None,
    "deadline_iso": None,
    "confidence": 1,
}


def real_schema(variant: str) -> dict[str, Any]:
    return json.loads(
        (Path(__file__).parent / "fixtures" / f"email_analysis_{variant}.json").read_text()
    )


def capture_worker(fallback: bool, output: dict[str, Any]) -> tuple[OllamaWorker, list[bytes]]:
    captured: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            body = {"data": [{"id": "test-model"}]}
        else:
            assert request.url.path == "/v1/chat/completions"
            captured.append(request.content)
            body = {
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}]
            }
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(body).encode()))

    worker = (
        LMStudioWorker("http://worker.invalid", "test-model", "synthetic-token", 30)
        if fallback
        else OllamaWorker("http://worker.invalid", "test-model")
    )
    worker._client = lambda timeout: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=httpx.MockTransport(handler), timeout=timeout, trust_env=False
    )
    return worker, captured


def legacy_wire(document: dict[str, Any], fallback: bool) -> bytes:
    """Pre-change ordinary-task payload: independent of the new projection/policy."""
    generation = document["generation"]
    task = document["task"]
    payload = {
        "model": "test-model",
        "messages": generation["messages"],
        "temperature": generation["temperature"],
        "max_tokens": document["requirements"]["max_output_tokens"],
        "stream": False,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": task["id"].replace(".", "_") + f"_v{task['version']}",
                "strict": True,
                "schema": generation["response_schema"],
            },
        },
    }
    if "seed" in generation:
        payload["seed"] = generation["seed"]
    if fallback:
        payload["ttl"] = 30
    return encode_json_bytes(payload)


def ingress(
    gateway: Any, document: dict[str, Any], fallback: bool, output: dict[str, Any]
) -> bytes:
    worker, captured = capture_worker(fallback, output)
    gateway.client.app.state.gateway_service.worker = worker
    before = deepcopy(document)
    response = gateway.client.post("/v1/inference", headers=gateway.headers, json=document)
    assert response.status_code == 200, response.json()
    assert document == before
    assert len(captured) == 1
    return captured[0]


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("sorted_input", [False, True])
@pytest.mark.parametrize("variant", ["main", "pr314"])
def test_real_email_schema_ingress_wire(
    gateway: Any, fallback: bool, sorted_input: bool, variant: str
) -> None:
    document = gateway.request()
    document["generation"]["response_schema"] = real_schema(variant)
    if sorted_input:
        document = json.loads(json.dumps(document, sort_keys=True))
    wire = ingress(gateway, document, fallback, EMAIL_OUTPUT)
    sent = json.loads(wire)
    schema = sent["response_format"]["json_schema"]["schema"]
    assert schema == document["generation"]["response_schema"]
    expected = DIRECT_ORDER if variant == "pr314" else SORTED_ORDER
    assert list(schema["properties"]) == expected
    if variant == "main":
        assert wire == legacy_wire(document, fallback)
        assert list(schema["properties"]) != DIRECT_ORDER
    # Ordering is the only payload difference; settings, name and values stay frozen.
    assert sent == json.loads(legacy_wire(document, fallback))


def nested_schema(route: str, required: list[str] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    child: dict[str, Any] = {
        "type": "object",
        "properties": {"a_last": {"type": "boolean"}, "z_first": {"type": "boolean"}},
        "additionalProperties": False,
    }
    if required is not None:
        child["required"] = required
    value: Any = {"a_last": False, "z_first": True}
    if route == "items":
        node = {"type": "array", "items": child, "maxItems": 1}
        value = [value]
    elif route == "anyOf":
        node = {"anyOf": [{"type": "null"}, child]}
    else:
        node = child
    return {
        "type": "object",
        "properties": {"a_last": {"type": "boolean"}, "z_first": node},
        "required": ["z_first", "a_last"],
        "additionalProperties": False,
    }, {"a_last": False, "z_first": value}


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("route", ["properties", "items", "anyOf"])
@pytest.mark.parametrize("required", [None, [], ["z_first"], ["z_first", "a_last"]])
def test_nested_eligibility_is_whole_request(
    gateway: Any, fallback: bool, route: str, required: list[str] | None
) -> None:
    schema, output = nested_schema(route, required)
    document = gateway.request()
    document["generation"]["response_schema"] = schema
    wire = ingress(gateway, document, fallback, output)
    sent = json.loads(wire)["response_format"]["json_schema"]["schema"]
    if required != ["z_first", "a_last"]:
        assert wire == legacy_wire(document, fallback)
        assert list(sent["properties"]) == ["a_last", "z_first"]
    else:
        assert list(sent["properties"]) == ["z_first", "a_last"]
        child = sent["properties"]["z_first"]
        if route == "items":
            child = child["items"]
        elif route == "anyOf":
            child = child["anyOf"][1]
        assert list(child["properties"]) == ["z_first", "a_last"]


@pytest.mark.parametrize("required", [None, [], ["z_first"]])
def test_partial_root_keeps_whole_sorted_request(gateway: Any, required: list[str] | None) -> None:
    schema, output = nested_schema("properties", ["z_first", "a_last"])
    if required is None:
        del schema["required"]
    else:
        schema["required"] = required
    document = gateway.request()
    document["generation"]["response_schema"] = schema
    assert ingress(gateway, document, False, output) == legacy_wire(document, False)


@pytest.mark.parametrize("variant", ["main", "pr314"])
def test_mapping_identity_and_required_sequence(gateway: Any, variant: str) -> None:
    document = gateway.request()
    document["generation"]["response_schema"] = real_schema(variant)
    original = InferenceRequest.model_validate(document)
    reordered = InferenceRequest.model_validate(json.loads(json.dumps(document, sort_keys=True)))
    changed_document = deepcopy(document)
    changed_document["generation"]["response_schema"]["required"].reverse()
    changed = InferenceRequest.model_validate(changed_document)
    worker, captured = capture_worker(False, EMAIL_OUTPUT)
    before = encode_json_bytes(original.model_dump(), sort_keys=False)
    for request in (original, reordered, changed):
        worker.infer(request, 30)
    assert before == encode_json_bytes(original.model_dump(), sort_keys=False)
    assert original.canonical_digest() == reordered.canonical_digest()
    assert captured[0] == captured[1]
    assert original.canonical_digest() != changed.canonical_digest()
    sent = json.loads(captured[2])["response_format"]["json_schema"]["schema"]
    if variant == "main":
        assert captured[2] == legacy_wire(changed_document, False)
        assert list(sent["properties"]) == SORTED_ORDER
    else:
        assert list(sent["properties"]) == list(reversed(DIRECT_ORDER))


@pytest.mark.parametrize("variant", ["main", "pr314"])
def test_email_order_replay_conflict_and_ack(gateway: Any, variant: str) -> None:
    document = gateway.request()
    document["generation"]["response_schema"] = real_schema(variant)
    worker, captured = capture_worker(False, EMAIL_OUTPUT)
    gateway.client.app.state.gateway_service.worker = worker
    first = gateway.client.post("/v1/inference", headers=gateway.headers, json=document)
    assert first.status_code == 200
    shuffled = json.loads(json.dumps(document, sort_keys=True))
    replay = gateway.client.post("/v1/inference", headers=gateway.headers, json=shuffled)
    assert replay.status_code == 200
    assert replay.json() == first.json()
    changed = deepcopy(document)
    changed["generation"]["response_schema"]["required"].reverse()
    conflict = gateway.client.post("/v1/inference", headers=gateway.headers, json=changed)
    assert conflict.status_code == 409
    assert conflict.json()["error"] == {"code": "invalid_request", "retryable": False}
    request_id = document["request_id"]
    ack = gateway.client.post(
        f"/v1/inference/{request_id}/ack",
        headers=gateway.headers,
        json={"protocol_version": 1, "request_id": request_id, "disposition": "persisted"},
    )
    assert ack.status_code == 200
    after = gateway.client.post("/v1/inference", headers=gateway.headers, json=document)
    assert after.status_code == 410
    assert len(captured) == 1


@pytest.mark.parametrize(
    "task_id",
    [
        "email.schedule.extract",
        "invoice.extract.batch",
        "document.summary.step",
    ],
)
@pytest.mark.parametrize("fallback", [False, True])
def test_sibling_wire_unchanged(gateway: Any, task_id: str, fallback: bool) -> None:
    document = gateway.request(task={"id": task_id, "version": 1})
    document["generation"]["response_schema"] = real_schema("pr314")
    worker, captured = capture_worker(fallback, EMAIL_OUTPUT)
    worker.infer(InferenceRequest.model_validate(document), 30)
    assert captured == [legacy_wire(document, fallback)]


def test_annotations_are_not_eligibility_nodes(gateway: Any) -> None:
    document = gateway.request()
    schema = real_schema("pr314")
    schema["default"] = {"type": "object", "properties": {"example": {}}, "required": False}
    document["generation"]["response_schema"] = schema
    wire = ingress(gateway, document, False, EMAIL_OUTPUT)
    sent = json.loads(wire)["response_format"]["json_schema"]["schema"]
    assert list(sent["properties"]) == DIRECT_ORDER
    assert sent["default"] == schema["default"]


def test_policy_requires_task_and_checks_coverage(gateway: Any) -> None:
    from local_inference_gateway.contracts import decoder_schema, uses_decoder_field_order

    schema = real_schema("pr314")
    for function in (decoder_schema, uses_decoder_field_order):
        with pytest.raises(TypeError, match="task"):
            function(schema)  # type: ignore[call-arg]
    # Same length does not establish coverage; undeclared required names are valid schema syntax.
    schema["required"][-1] = "undeclared"
    InferenceRequest.model_validate(
        {
            **gateway.request(),
            "generation": {
                **gateway.request()["generation"],
                "response_schema": schema,
            },
        }
    )
    assert not uses_decoder_field_order(schema, task=("email.analyze", 1))
    assert not uses_decoder_field_order(real_schema("pr314"), task=("email.analyze", 2))
    for empty in ({"type": "object"}, {"type": "object", "properties": {}, "required": []}):
        assert uses_decoder_field_order(empty, task=("email.analyze", 1))


@pytest.mark.parametrize("required", [0, "", False, ["category", "category"]])
def test_malformed_required_still_rejected_before_dispatch(gateway: Any, required: Any) -> None:
    document = gateway.request()
    document["generation"]["response_schema"] = real_schema("pr314")
    document["generation"]["response_schema"]["required"] = required
    worker, captured = capture_worker(False, EMAIL_OUTPUT)
    gateway.client.app.state.gateway_service.worker = worker
    response = gateway.client.post("/v1/inference", headers=gateway.headers, json=document)
    assert response.status_code == 422
    assert captured == []


@pytest.mark.parametrize("variant", ["main", "pr314"])
def test_email_order_expiry_does_not_dispatch_again(gateway: Any, variant: str) -> None:
    document = gateway.request()
    document["generation"]["response_schema"] = real_schema(variant)
    worker, captured = capture_worker(False, EMAIL_OUTPUT)
    gateway.client.app.state.gateway_service.worker = worker
    first = gateway.client.post("/v1/inference", headers=gateway.headers, json=document)
    assert first.status_code == 200
    gateway.clock.advance(301)
    expired = gateway.client.post("/v1/inference", headers=gateway.headers, json=document)
    assert expired.status_code == 409
    assert expired.json()["error"]["code"] == "request_expired"
    assert len(captured) == 1


@pytest.mark.parametrize("count", [0, 1, 100])
def test_empty_single_and_large_valid_object(gateway: Any, count: int) -> None:
    names = [f"field_{i:03d}" for i in range(count)]
    document = gateway.request()
    document["generation"]["response_schema"] = {
        "type": "object",
        "properties": {name: {"type": "boolean"} for name in names},
        "required": list(reversed(names)),
        "additionalProperties": False,
    }
    wire = ingress(gateway, document, False, dict.fromkeys(names, False))
    sent = json.loads(wire)["response_format"]["json_schema"]["schema"]
    assert list(sent["properties"]) == list(reversed(names))
