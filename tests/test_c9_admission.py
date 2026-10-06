from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from local_inference_gateway.contracts import (
    _validate_supported_schema,
    encode_json_bytes,
    is_bounded_root_object_choice,
)
from tests.test_passage_schema import admitted_request, passage_schema


def choice_schema(*costs: int) -> dict[str, Any]:
    template = passage_schema(1)
    definitions = template.pop("$defs")
    branches = []
    for cost in costs:
        branch = deepcopy(template)
        branch["properties"]["z_source"] = {
            "type": "array",
            "maxItems": cost,
            "items": {"$ref": "#/$defs/passage"},
        }
        branches.append(branch)
    return {"$defs": definitions, "anyOf": branches}


def test_c9_regression_root_choice_with_shared_definitions(gateway: Any) -> None:
    schema = choice_schema(1, 1)
    assert admitted_request(gateway, schema).generation.response_schema == schema


def test_c9_regression_alternative_budget_at_origin() -> None:
    schema = choice_schema(32, 32)
    # Isolate counting from the independent root/definitions admission defect.
    _validate_supported_schema(
        {"anyOf": schema["anyOf"]},
        definitions=schema["$defs"],
        references=[0, 0],
        multiplicity=1,
        allow_root_object_choice=True,
    )


def test_c9_regression_whole_passage_over_old_limit(gateway: Any) -> None:
    schema = passage_schema(1)
    schema["$defs"]["passage"]["enum"] = ["x" * 241]
    assert admitted_request(gateway, schema).generation.response_schema == schema


@pytest.mark.parametrize("costs", [(31, 31), (32, 32), (0, 32), (32, 0), (0, 0)])
def test_alternatives_use_largest_possible_output(gateway: Any, costs: tuple[int, ...]) -> None:
    admitted_request(gateway, choice_schema(*costs))


@pytest.mark.parametrize("costs", [(32, 33), (33, 32), (0, 33)])
def test_every_alternative_must_fit(gateway: Any, costs: tuple[int, ...]) -> None:
    with pytest.raises(ValidationError, match="comparison budget"):
        admitted_request(gateway, choice_schema(*costs))


def test_admission_does_not_add_a_character_weighted_output_budget(gateway: Any) -> None:
    schema = choice_schema(32, 32)
    schema["$defs"]["passage"]["enum"] = ["x" * 4096]
    admitted_request(gateway, schema)


@pytest.mark.parametrize("extra,valid", [(0, True), (1, False)])
def test_choice_keeps_sibling_array_and_nullable_costs(
    gateway: Any, extra: int, valid: bool
) -> None:
    schema = choice_schema(1, 1)
    branch = schema["anyOf"][1]
    branch["properties"]["z_source"] = {
        "type": "array",
        "maxItems": 4,
        "items": {
            "anyOf": [
                {"type": "array", "maxItems": 8, "items": {"$ref": "#/$defs/passage"}},
                {"type": "null"},
            ]
        },
    }
    branch["properties"]["sibling"] = {
        "type": "array",
        "maxItems": extra,
        "items": {"$ref": "#/$defs/passage"},
    }
    branch["required"].append("sibling")
    if valid:
        admitted_request(gateway, schema)
    else:
        with pytest.raises(ValidationError, match="comparison budget"):
            admitted_request(gateway, schema)


@pytest.mark.parametrize("references", [15, 16, 17])
def test_physical_references_still_sum_across_alternatives(gateway: Any, references: int) -> None:
    schema = choice_schema(1, 1)
    for branch, count in zip(schema["anyOf"], [7, references - 7], strict=True):
        branch["properties"] = {f"p{i}": {"$ref": "#/$defs/passage"} for i in range(count)}
        branch["required"] = list(branch["properties"])
    if references <= 16:
        admitted_request(gateway, schema)
    else:
        with pytest.raises(ValidationError, match="reference limit"):
            admitted_request(gateway, schema)


@pytest.mark.parametrize("count", [1, 2, 64, 65])
def test_root_choice_count_and_shared_policy_classifier(gateway: Any, count: int) -> None:
    schema = choice_schema(*([0] * count))
    for branch in schema["anyOf"]:
        branch["properties"]["z_source"]["items"] = {"type": "string"}
    assert is_bounded_root_object_choice(schema) == (2 <= count <= 64)
    if 2 <= count <= 64:
        admitted_request(gateway, schema)
        schema.pop("$defs")
        admitted_request(gateway, schema)
    else:
        with pytest.raises(ValidationError):
            admitted_request(gateway, schema)


@pytest.mark.parametrize("length", [0, 1, 239, 240, 241, 4095, 4096, 4097])
@pytest.mark.parametrize("character", ["x", "\N{LATIN SMALL LETTER E WITH ACUTE}"])
def test_passage_length_counts_characters(gateway: Any, length: int, character: str) -> None:
    schema = choice_schema(1, 1)
    schema["$defs"]["passage"]["enum"] = ["ordinary", character * length]
    if 1 <= length <= 4096:
        admitted_request(gateway, schema)
    else:
        with pytest.raises(ValidationError, match="bounded unique strings"):
            admitted_request(gateway, schema)


def test_all_forbidden_forms_remain_rejected_in_choice_roots(gateway: Any) -> None:
    invalid = []
    for key, value in [
        ("oneOf", []),
        ("allOf", []),
        ("$id", "https://example.invalid"),
        ("type", "object"),
        ("title", "root sibling"),
    ]:
        schema = choice_schema(1, 1)
        schema[key] = value
        invalid.append(schema)
    for definitions in [
        None,
        False,
        0,
        "",
        [],
        {},
        {f"d{i}": {"type": "string", "enum": ["x"]} for i in range(3)},
    ]:
        schema = choice_schema(1, 1)
        schema["$defs"] = definitions
        invalid.append(schema)
    for leaf in [
        {"$ref": "#/$defs/passage"},
        {"type": "object"},
        {"type": "string", "enum": ["x"], "pattern": ".*"},
        {"type": "string", "enum": ["ok", False]},
        {"type": "string", "enum": ["same", "same"]},
    ]:
        schema = choice_schema(1, 1)
        schema["$defs"]["unused"] = leaf
        invalid.append(schema)
    for ref in [
        "https://example.invalid/leaf",
        "file:///tmp/leaf",
        "passage",
        "#",
        "#/$defs/missing",
        "#/$defs/passage/enum/0",
        "#/$defs/passage%00",
    ]:
        schema = choice_schema(1, 1)
        schema["anyOf"][1]["properties"]["z_source"]["items"]["$ref"] = ref
        invalid.append(schema)
    for bad in [
        None,
        False,
        {"type": "string"},
        {"type": "object"},
        {"type": "object", "additionalProperties": True},
    ]:
        schema = choice_schema(1, 1)
        schema["anyOf"][1] = bad
        invalid.append(schema)
    for bad_items in [
        {"$ref": "#/$defs/passage", "type": "string"},
        {"$defs": {"inner": {"type": "string", "enum": ["x"]}}},
        {"anyOf": [{"type": "object", "additionalProperties": False}] * 2},
    ]:
        schema = choice_schema(0, 0)
        schema["anyOf"][1]["properties"]["z_source"]["items"] = bad_items
        invalid.append(schema)
    for schema in invalid:
        with pytest.raises(ValidationError):
            admitted_request(gateway, schema)
    schema = choice_schema(1, 1)
    schema["$defs"]["unused"] = {"type": "string", "enum": ["checked unused leaf"]}
    admitted_request(gateway, schema)


@pytest.mark.parametrize("size", [249999, 250000, 250001])
def test_choice_schema_byte_boundary(gateway: Any, size: int) -> None:
    schema = choice_schema(1, 1)
    schema["anyOf"][0]["title"] = ""
    schema["anyOf"][0]["title"] = "x" * (size - len(encode_json_bytes(schema)))
    assert len(encode_json_bytes(schema)) == size
    if size <= 250000:
        admitted_request(gateway, schema)
    else:
        with pytest.raises(ValidationError, match="byte limit"):
            admitted_request(gateway, schema)


@pytest.mark.parametrize("depth", [31, 32, 33])
def test_choice_keeps_total_depth_boundary(gateway: Any, depth: int) -> None:
    schema = choice_schema(1, 1)
    value: Any = None
    # default lives at root(1)/anyOf(2)/branch(3)/default(4).
    for _ in range(depth - 4):
        value = [value]
    schema["anyOf"][0]["default"] = value
    if depth <= 32:
        admitted_request(gateway, schema)
    else:
        with pytest.raises(ValidationError, match="depth limit"):
            admitted_request(gateway, schema)


@pytest.mark.parametrize("nodes", [19999, 20000, 20001])
def test_choice_keeps_total_node_boundary(gateway: Any, nodes: int) -> None:
    schema = choice_schema(1, 1)
    schema["anyOf"][0]["default"] = []

    def size(value: Any) -> int:
        if isinstance(value, dict):
            return 1 + sum(size(item) for item in value.values())
        if isinstance(value, list):
            return 1 + sum(size(item) for item in value)
        return 1

    schema["anyOf"][0]["default"] = [None] * (nodes - size(schema))
    assert size(schema) == nodes
    if nodes <= 20000:
        admitted_request(gateway, schema)
    else:
        with pytest.raises(ValidationError, match="node limit"):
            admitted_request(gateway, schema)


def c9_schema() -> dict[str, Any]:
    """Public synthetic instance of DocSum's frozen eight relation/list shapes."""
    shapes = [
        ("preserved", True, True),
        ("changed", True, True),
        ("omitted", True, False),
        ("omitted", True, True),
        ("uncertain", False, True),
        ("uncertain", True, False),
        ("uncertain", True, True),
        ("not_applicable", False, False),
    ]
    choices = []
    for relation, source, claim in shapes:
        properties = {}
        for name, present in [("source", source), ("claim", claim)]:
            properties[f"{name}_spans"] = {
                "type": "array",
                "minItems": int(present),
                "maxItems": 4 if present else 0,
                "items": {"$ref": f"#/$defs/{name}_span"},
            }
        properties["relation"] = {"type": "string", "enum": [relation]}
        choices.append(
            {
                "type": "object",
                "properties": properties,
                "required": ["source_spans", "claim_spans", "relation"],
                "additionalProperties": False,
            }
        )
    return {
        "anyOf": choices,
        "$defs": {
            "source_span": {"type": "string", "enum": ["s" * 4096]},
            "claim_span": {"type": "string", "enum": ["claim"]},
        },
    }


def test_c9_relation_shapes_and_decoder_order(gateway: Any) -> None:
    import json

    from jsonschema import Draft202012Validator

    from local_inference_gateway.contracts import decoder_schema

    schema = c9_schema()
    request = admitted_request(gateway, schema)
    shuffled = admitted_request(gateway, json.loads(json.dumps(schema, sort_keys=True)))
    wire = encode_json_bytes(decoder_schema(request.generation.response_schema), sort_keys=False)
    assert wire == encode_json_bytes(
        decoder_schema(shuffled.generation.response_schema), sort_keys=False
    )
    assert request.canonical_digest() == shuffled.canonical_digest()
    for branch in json.loads(wire)["anyOf"]:
        assert list(branch["properties"]) == ["source_spans", "claim_spans", "relation"]
    validator = Draft202012Validator(schema)
    allowed = {
        (
            b["properties"]["relation"]["enum"][0],
            bool(b["properties"]["source_spans"]["minItems"]),
            bool(b["properties"]["claim_spans"]["minItems"]),
        )
        for b in schema["anyOf"]
    }
    for relation in ["preserved", "changed", "omitted", "uncertain", "not_applicable"]:
        for source in [False, True]:
            for claim in [False, True]:
                output = {
                    "source_spans": ["s" * 4096] if source else [],
                    "claim_spans": ["claim"] if claim else [],
                    "relation": relation,
                }
                assert validator.is_valid(output) == ((relation, source, claim) in allowed)


@pytest.mark.parametrize("failure", ["length_valid", "length_truncated", "foreign", "relation"])
def test_document_v2_bad_output_cannot_complete_or_replay(gateway: Any, failure: str) -> None:
    import json
    import sqlite3

    import httpx

    from local_inference_gateway.contracts import decoder_schema
    from local_inference_gateway.store import RequestStore
    from tests.test_passage_schema import passage_client
    from tests.test_worker import worker_with_handler

    request = admitted_request(gateway, c9_schema()).model_dump(mode="json", exclude_none=True)
    request["requirements"]["max_output_tokens"] = 4096
    output = {"source_spans": ["s" * 4096], "claim_spans": ["claim"], "relation": "preserved"}
    good_body = json.dumps(output)
    bad_body = good_body
    if failure == "length_truncated":
        bad_body = good_body[:-1]
    elif failure == "foreign":
        bad_body = json.dumps({**output, "source_spans": ["foreign"]})
    elif failure == "relation":
        bad_body = json.dumps({**output, "source_spans": []})
    calls: list[dict[str, Any]] = []

    def handler(incoming: httpx.Request) -> httpx.Response:
        if incoming.url.path == "/api/show":
            body = {"parameters": "num_ctx 32768"}
        elif incoming.url.path == "/api/ps":
            body = {"models": []}
        else:
            assert incoming.url.path == "/v1/chat/completions"
            calls.append(json.loads(incoming.content))
            first = len(calls) == 1
            body = {
                "choices": [
                    {
                        "finish_reason": "length"
                        if first and failure.startswith("length")
                        else "stop",
                        "message": {"content": bad_body if first else good_body},
                    }
                ]
            }
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(body).encode()))

    gateway.worker = worker_with_handler(handler)
    expected = {"code": "invalid_worker_output", "retryable": False}
    with passage_client(gateway) as client:
        for _ in range(2):
            response = client.post("/v1/inference", headers=gateway.other_headers, json=request)
            assert response.status_code == 502
            assert response.json()["status"] == "failed"
            assert response.json()["error"] == expected
            assert "output" not in response.json()
            assert "provenance" not in response.json()
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 4096
    wire_schema = calls[0]["response_format"]["json_schema"]["schema"]
    assert encode_json_bytes(wire_schema, sort_keys=False) == encode_json_bytes(
        decoder_schema(c9_schema()), sort_keys=False
    )
    with sqlite3.connect(gateway.settings.database_path) as db:
        row = db.execute(
            "SELECT state, output_nonce, output_ciphertext, output_media_type, "
            "producing_deployment_id, producing_task_policy_version "
            "FROM inference_requests WHERE request_id = ?",
            (request["request_id"],),
        ).fetchone()
    assert row == ("failed", None, None, None, None, None)
    gateway.store = RequestStore(
        gateway.settings.database_path,
        gateway.store.cipher,
        max_open_total=16,
        max_open_per_credential=4,
        tombstone_retention_seconds=86400,
    )
    with passage_client(gateway) as client:
        response = client.post("/v1/inference", headers=gateway.other_headers, json=request)
        assert response.status_code == 502
        assert response.json()["status"] == "failed"
        assert response.json()["error"] == expected
        assert "output" not in response.json()
        assert len(calls) == 1
        request["request_id"] = "22345678-1234-4234-8234-123456789abc"
        completed = client.post("/v1/inference", headers=gateway.other_headers, json=request)
        replay = client.post("/v1/inference", headers=gateway.other_headers, json=request)
        assert completed.status_code == replay.status_code == 200
        assert completed.json() == replay.json()
        assert completed.json()["status"] == "completed"
        assert completed.json()["output"]["content"] == good_body
        assert completed.json()["provenance"]["task_policy_version"] == 2
        assert len(calls) == 2


def test_choice_passages_do_not_escape_task_policy(gateway: Any, monkeypatch: Any) -> None:
    request = admitted_request(gateway, c9_schema()).model_dump(mode="json", exclude_none=True)

    def no_effect(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("rejected request must not reserve durable work")

    with monkeypatch.context() as denied:
        denied.setattr(gateway.store, "admit", no_effect)
        for headers in [gateway.headers, gateway.other_headers, gateway.invoice_headers]:
            assert (
                gateway.client.post("/v1/inference", headers=headers, json=request).status_code
                == 403
            )
        assert gateway.client.post("/v1/inference", json=request).status_code == 401
        for task, headers, temperature in [
            ("document.summary.step", gateway.other_headers, 0.0),
            ("email.analyze", gateway.headers, 0.1),
            ("email.schedule.extract", gateway.headers, 0.1),
            ("invoice.extract.batch", gateway.invoice_headers, 0.0),
        ]:
            request["task"] = {"id": task, "version": 1}
            request["generation"]["temperature"] = temperature
            response = gateway.client.post("/v1/inference", headers=headers, json=request)
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "unsupported_task"
        assert gateway.worker.calls == 0
    # A valid ordinary request for each existing task still reaches dispatch.
    for index, (task, headers, temperature) in enumerate(
        [
            ("document.summary.step", gateway.other_headers, 0.0),
            ("email.analyze", gateway.headers, 0.1),
            ("email.schedule.extract", gateway.headers, 0.1),
            ("invoice.extract.batch", gateway.invoice_headers, 0.0),
        ]
    ):
        ordinary = gateway.request(
            task={"id": task, "version": 1},
            request_id=f"{index}2345678-1234-4234-8234-123456789abc",
        )
        ordinary["generation"]["temperature"] = temperature
        assert (
            gateway.client.post("/v1/inference", headers=headers, json=ordinary).status_code == 200
        )
    assert gateway.worker.calls == 4
