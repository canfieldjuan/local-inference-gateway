from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from local_inference_gateway.contracts import InferenceRequest
from tests.test_worker import worker_with_handler


def passage_schema(count: int = 101) -> dict[str, Any]:
    return {
        "type": "object",
        "$defs": {"passage": {"type": "string", "enum": [f"value-{i}" for i in range(count)]}},
        "properties": {
            "a_relation": {"type": "string", "enum": ["preserved"]},
            "z_source": {"$ref": "#/$defs/passage"},
        },
        "required": ["z_source", "a_relation"],
        "additionalProperties": False,
    }


def test_shared_passage_schema_is_admitted(gateway: Any) -> None:
    document = gateway.request(task={"id": "document.summary.step", "version": 2})
    document["generation"]["temperature"] = 0.0
    document["generation"]["response_schema"] = passage_schema()
    admitted = InferenceRequest.model_validate(document)
    assert admitted.generation.response_schema == passage_schema()


def test_shared_passage_decoder_receives_required_order(gateway: Any) -> None:
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=httpx.ByteStream(
                json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": '{"z_source":"value-0","a_relation":"preserved"}'
                                },
                                "finish_reason": "stop",
                            }
                        ]
                    }
                ).encode()
            ),
        )

    request = InferenceRequest.model_validate(gateway.request())
    # Isolate the wire boundary while the separate admission regression is red.
    request.generation.response_schema = passage_schema()
    worker_with_handler(handler).infer(request, 30)
    schema = captured[0]["response_format"]["json_schema"]["schema"]
    assert list(schema["properties"]) == ["z_source", "a_relation"]


def test_shared_passage_array_product_is_bounded(gateway: Any) -> None:
    import pytest
    from pydantic import ValidationError

    document = gateway.request(task={"id": "document.summary.step", "version": 2})
    schema = passage_schema(1)
    schema["properties"]["z_source"] = {
        "type": "array",
        "maxItems": 8,
        "items": {"type": "array", "maxItems": 8, "items": {"$ref": "#/$defs/passage"}},
    }
    document["generation"]["response_schema"] = schema
    with pytest.raises(ValidationError, match="passage comparison budget"):
        InferenceRequest.model_validate(document)


def admitted_request(gateway: Any, schema: dict[str, Any]) -> InferenceRequest:
    document = gateway.request(task={"id": "document.summary.step", "version": 2})
    document["generation"]["temperature"] = 0.0
    document["generation"]["response_schema"] = schema
    return InferenceRequest.model_validate(document)


def test_passage_boundaries_and_ordinary_enum_limit(gateway: Any) -> None:
    import pytest
    from pydantic import ValidationError

    for count in [1, 100, 101, 8191, 8192]:
        admitted_request(gateway, passage_schema(count))
    for count in [0, 8193]:
        with pytest.raises(ValidationError, match="bounded unique strings"):
            admitted_request(gateway, passage_schema(count))
    for value in [0, False, None, "", [], {}, "x" * 4097]:
        schema = passage_schema(1)
        schema["$defs"]["passage"]["enum"] = [value]
        with pytest.raises(ValidationError):
            admitted_request(gateway, schema)
    for value in ["x", "x" * 239, "x" * 240, "café"]:
        schema = passage_schema(1)
        schema["$defs"]["passage"]["enum"] = [value]
        admitted_request(gateway, schema)
    schema = passage_schema(1)
    schema["$defs"]["passage"]["enum"] = ["same", "same"]
    with pytest.raises(ValidationError):
        admitted_request(gateway, schema)
    schema = passage_schema(1)
    schema["properties"]["a_relation"]["enum"] = [str(i) for i in range(101)]
    with pytest.raises(ValidationError, match="bounded scalar"):
        admitted_request(gateway, schema)


def test_definition_shape_and_reference_ownership(gateway: Any) -> None:
    from copy import deepcopy

    import pytest
    from pydantic import ValidationError

    for name in ["a", "_", "a" * 64]:
        schema = passage_schema(1)
        schema["$defs"][name] = schema["$defs"].pop("passage")
        schema["properties"]["z_source"]["$ref"] = f"#/$defs/{name}"
        admitted_request(gateway, schema)
    invalid = []
    for name in ["", "a" * 65, "a/b", "a~b", "../passage", "café", "a.b"]:
        schema = passage_schema(1)
        schema["$defs"] = {name: schema["$defs"]["passage"]}
        invalid.append(schema)
    for defs in [
        None,
        False,
        0,
        "",
        [],
        {},
        {str(i): {"type": "string", "enum": ["x"]} for i in range(3)},
    ]:
        schema = passage_schema(1)
        schema["$defs"] = defs
        invalid.append(schema)
    for reference in [
        "https://example.invalid/schema",
        "file:///tmp/schema",
        "#",
        "#/properties/z_source",
        "#/$defs/missing",
        "#/$defs/passage/enum/0",
        "#/$defs/passage%00",
        False,
        0,
        None,
        "",
    ]:
        schema = passage_schema(1)
        schema["properties"]["z_source"] = {"$ref": reference}
        invalid.append(schema)
    for sibling in [{"type": "string"}, {"enum": ["value-0"]}, {"$id": "https://example.invalid/"}]:
        schema = passage_schema(1)
        schema["properties"]["z_source"].update(sibling)
        invalid.append(schema)
    for definition in [
        {"$ref": "#/$defs/passage"},
        {"type": "array", "maxItems": 1, "items": {"$ref": "#/$defs/passage"}},
        {"type": "string", "enum": ["x"], "pattern": ".*"},
        {"type": "object"},
    ]:
        schema = passage_schema(1)
        schema["$defs"]["unused"] = definition
        invalid.append(schema)
    schema = passage_schema(1)
    schema["$defs"]["other"] = {"$ref": "#/$defs/passage"}
    schema["$defs"]["passage"] = {"$ref": "#/$defs/other"}
    invalid.append(schema)
    schema = passage_schema(1)
    schema["properties"]["a_relation"] = deepcopy(schema)
    invalid.append(schema)
    for schema in invalid:
        with pytest.raises(ValidationError):
            admitted_request(gateway, schema)
    schema = passage_schema(1)
    schema["$defs"]["unused"] = {"type": "string", "enum": ["checked even when unused"]}
    admitted_request(gateway, schema)


def test_reference_and_multiplicity_boundaries(gateway: Any) -> None:
    import pytest
    from pydantic import ValidationError

    for count in [0, 1, 15, 16, 17]:
        schema = passage_schema(1)
        schema["properties"] = {f"p{i}": {"$ref": "#/$defs/passage"} for i in range(count)}
        schema["required"] = list(schema["properties"])
        if count <= 16:
            admitted_request(gateway, schema)
        else:
            with pytest.raises(ValidationError, match="reference limit"):
                admitted_request(gateway, schema)
    for count in [0, 1, 31, 32, 33, 100, 101, -1, None, False, ""]:
        schema = passage_schema(1)
        schema["properties"]["z_source"] = {
            "type": "array",
            "maxItems": count,
            "items": {"$ref": "#/$defs/passage"},
        }
        if type(count) is int and 0 <= count <= 32:
            admitted_request(gateway, schema)
        else:
            with pytest.raises(ValidationError):
                admitted_request(gateway, schema)
    # C9's mixed dimensions: eight lists with four entries each, not only one reference.
    schema = passage_schema(1)
    schema["properties"] = {
        f"p{i}": {"type": "array", "maxItems": 4, "items": {"$ref": "#/$defs/passage"}}
        for i in range(8)
    }
    schema["required"] = list(schema["properties"])
    admitted_request(gateway, schema)
    schema["properties"]["p0"]["maxItems"] = 5
    with pytest.raises(ValidationError, match="comparison budget"):
        admitted_request(gateway, schema)
    # Carry outer array multiplicity through object and nullable branches too.
    for branch in [
        {"type": "object", "properties": {"value": {"$ref": "#/$defs/passage"}}},
        {
            "anyOf": [
                {"type": "object", "properties": {"value": {"$ref": "#/$defs/passage"}}},
                {"type": "null"},
            ]
        },
    ]:
        schema = passage_schema(1)
        schema["properties"]["z_source"] = {"type": "array", "maxItems": 33, "items": branch}
        with pytest.raises(ValidationError, match="comparison budget"):
            admitted_request(gateway, schema)


def test_shared_schema_keeps_global_byte_node_depth_bounds(gateway: Any) -> None:
    import pytest
    from pydantic import ValidationError

    from local_inference_gateway.contracts import MAX_SCHEMA_BYTES, encode_json_bytes

    schema = passage_schema(1)
    schema["title"] = ""
    schema["title"] = "x" * (MAX_SCHEMA_BYTES - len(encode_json_bytes(schema)))
    assert len(encode_json_bytes(schema)) == MAX_SCHEMA_BYTES
    admitted_request(gateway, schema)
    schema["title"] += "x"
    with pytest.raises(ValidationError, match="byte limit"):
        admitted_request(gateway, schema)
    schema = passage_schema(1)
    schema["default"] = [None] * 20_000
    with pytest.raises(ValidationError, match="node limit"):
        admitted_request(gateway, schema)
    schema = passage_schema(1)
    child = schema["properties"]["z_source"]
    for _ in range(32):
        child.update({"type": "object", "properties": {"child": {}}})
        child = child["properties"]["child"]
    with pytest.raises(ValidationError, match="depth limit"):
        admitted_request(gateway, schema)


def test_decoder_order_and_identity_have_separate_owners(gateway: Any) -> None:
    from local_inference_gateway.contracts import decoder_schema, encode_json_bytes

    schema = passage_schema(1)
    nested = {
        "type": "object",
        "properties": {"b": {"type": "string"}, "z": {"type": "string"}},
        "required": ["z", "b"],
    }
    schema["properties"]["optional"] = nested
    first = admitted_request(gateway, schema)
    reordered = admitted_request(gateway, json.loads(json.dumps(schema, sort_keys=True)))
    before = first.canonical_digest()
    assert before == reordered.canonical_digest()
    first_wire = encode_json_bytes(
        decoder_schema(first.generation.response_schema), sort_keys=False
    )
    second_wire = encode_json_bytes(
        decoder_schema(reordered.generation.response_schema), sort_keys=False
    )
    assert first_wire == second_wire
    assert first.canonical_digest() == before
    properties = json.loads(first_wire)["properties"]
    assert list(properties) == ["z_source", "a_relation", "optional"]
    assert list(properties["optional"]["properties"]) == ["z", "b"]
    schema["required"].reverse()
    assert admitted_request(gateway, schema).canonical_digest() != before


def passage_client(gateway: Any) -> Any:
    import hashlib

    from fastapi.testclient import TestClient

    from local_inference_gateway.app import create_app
    from local_inference_gateway.config import Credential, CredentialStore
    from tests.conftest import OTHER_TOKEN

    credential = Credential(
        hashlib.sha256(b"passage-test").hexdigest(),
        hashlib.sha256(OTHER_TOKEN.encode()).hexdigest(),
        frozenset({("document.summary.step", 2)}),
    )
    return TestClient(
        create_app(
            gateway.settings,
            credentials=CredentialStore((credential,)),
            store=gateway.store,
            worker=gateway.worker,
            clock=gateway.clock,
        )
    )


def test_new_task_requires_its_own_grant_and_old_tasks_keep_their_policy(
    gateway: Any, monkeypatch: Any
) -> None:
    def no_effect(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("rejected request must not reserve durable work")

    monkeypatch.setattr(gateway.store, "admit", no_effect)
    request = admitted_request(gateway, passage_schema(1)).model_dump(
        mode="json", exclude_none=True
    )
    for headers in [gateway.headers, gateway.other_headers, gateway.invoice_headers]:
        assert (
            gateway.client.post("/v1/inference", headers=headers, json=request).status_code == 403
        )
    assert gateway.client.post("/v1/inference", json=request).status_code == 401
    for task, headers, temperature in [
        (("document.summary.step", 1), gateway.other_headers, 0.0),
        (("email.analyze", 1), gateway.headers, 0.1),
        (("invoice.extract.batch", 1), gateway.invoice_headers, 0.0),
    ]:
        request["task"] = {"id": task[0], "version": task[1]}
        request["generation"]["temperature"] = temperature
        reply = gateway.client.post("/v1/inference", headers=headers, json=request)
        assert reply.status_code == 422
        assert reply.json()["error"]["code"] == "unsupported_task"
    with passage_client(gateway) as client:
        assert (
            client.get("/v1/health", headers=gateway.other_headers).json()["tasks"][0]["version"]
            == 2
        )
        request["task"] = {"id": "document.summary.step", "version": 2}
        request["requirements"]["max_output_tokens"] = 4097
        reply = client.post("/v1/inference", headers=gateway.other_headers, json=request)
        assert reply.status_code == 422
        assert reply.json()["error"]["code"] == "unsupported_task"
    assert gateway.worker.calls == 0
    assert all(
        t["version"] == 1
        for t in gateway.client.get("/v1/health", headers=gateway.other_headers).json()["tasks"]
    )


@pytest.mark.parametrize("root_choice", [False, True])
def test_passage_task_concurrent_replay_collision_and_ack(gateway: Any, root_choice: bool) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from copy import deepcopy

    schema = passage_schema(1)
    if root_choice:
        definitions = schema.pop("$defs")
        schema = {"$defs": definitions, "anyOf": [schema, deepcopy(schema)]}
    request = admitted_request(gateway, schema).model_dump(mode="json", exclude_none=True)
    request["requirements"]["max_output_tokens"] = 4096
    gateway.worker.result_content = '{"z_source":"value-0","a_relation":"preserved"}'
    gateway.worker.release.clear()
    with passage_client(gateway) as client, ThreadPoolExecutor(max_workers=2) as pool:

        def post(document: dict[str, Any]) -> Any:
            return client.post("/v1/inference", headers=gateway.other_headers, json=document)

        first = pool.submit(post, request)
        assert gateway.worker.started.wait(3)
        second = pool.submit(post, json.loads(json.dumps(request, sort_keys=True)))
        gateway.worker.release.set()
        a, b = first.result(timeout=5), second.result(timeout=5)
        assert a.status_code == b.status_code == 200
        assert a.json() == b.json()
        assert gateway.worker.calls == 1
        changed = deepcopy(request)
        changed["generation"]["response_schema"]["$defs"]["passage"]["enum"] = ["other"]
        assert post(changed).status_code == 409
        ack = client.post(
            f"/v1/inference/{request['request_id']}/ack",
            headers=gateway.other_headers,
            json={
                "protocol_version": 1,
                "request_id": request["request_id"],
                "disposition": "persisted",
            },
        )
        assert ack.status_code == 200
        assert gateway.store.raw_persisted_values(request["request_id"]) == (None, None)
        gateway.clock.advance(301)
        assert post(request).status_code == 409
        assert gateway.worker.calls == 1


def test_foreign_passage_output_fails_actual_worker_validator(gateway: Any) -> None:
    import pytest

    from local_inference_gateway.worker import InvalidWorkerOutput

    request = admitted_request(gateway, passage_schema(1))

    def handler(http_request: httpx.Request) -> httpx.Response:
        if http_request.url.path in {"/api/show", "/api/ps"}:
            metadata = (
                {"parameters": "num_ctx 32768"}
                if http_request.url.path == "/api/show"
                else {"models": []}
            )
            return httpx.Response(200, stream=httpx.ByteStream(json.dumps(metadata).encode()))
        document = {
            "choices": [
                {
                    "message": {"content": '{"z_source":"foreign","a_relation":"preserved"}'},
                    "finish_reason": "stop",
                }
            ]
        }
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=httpx.ByteStream(json.dumps(document).encode()),
        )

    with pytest.raises(InvalidWorkerOutput, match="does not match response schema"):
        worker_with_handler(handler).infer(request, 30)


def test_decoder_treats_annotations_as_data(gateway: Any) -> None:
    from local_inference_gateway.contracts import decoder_schema

    schema = passage_schema(1)
    schema["default"] = {
        "required": False,
        "properties": {"anything": {"$ref": "https://example.invalid/annotation"}},
    }
    admitted = admitted_request(gateway, schema)
    result = decoder_schema(admitted.generation.response_schema)
    assert result["default"] == schema["default"]
    assert list(result["properties"]) == ["z_source", "a_relation"]
