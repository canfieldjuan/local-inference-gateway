from __future__ import annotations

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from local_inference_gateway.contracts import InferenceRequest, encode_json_bytes, parse_json_object


def test_canonical_digest_is_stable_for_key_order(gateway) -> None:  # type: ignore[no-untyped-def]
    first = gateway.request()
    second = json.loads(json.dumps(first, sort_keys=True))

    assert (
        InferenceRequest.model_validate(first).canonical_digest()
        == InferenceRequest.model_validate(second).canonical_digest()
    )


def test_canonical_digest_preserves_exact_schema_numbers(gateway) -> None:  # type: ignore[no-untyped-def]
    exact = parse_json_object(
        json.dumps(gateway.request())
        .replace(
            '"response_schema": {"type": "object"}',
            '"response_schema": {"type": "object", "enum": [1e-999]}',
        )
        .encode()
    )
    zero = parse_json_object(
        json.dumps(gateway.request())
        .replace(
            '"response_schema": {"type": "object"}',
            '"response_schema": {"type": "object", "enum": [0.0]}',
        )
        .encode()
    )

    exact_request = InferenceRequest.model_validate(exact)
    zero_request = InferenceRequest.model_validate(zero)

    assert exact_request.canonical_digest() != zero_request.canonical_digest()
    assert b'"enum":[1e-999]' in encode_json_bytes(exact_request.generation.response_schema)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("protocol_version",), True),
        (("task", "version"), True),
        (("requirements", "max_output_tokens"), True),
        (("request_id",), "12345678-1234-4234-8234-123456789ABC"),
        (("request_id",), "12345678-1234-1234-8234-123456789abc"),
        (("request_expires_at",), "2026-09-10T18:05:00+00:00"),
    ],
)
def test_contract_rejects_ambiguous_identity_and_versions(
    gateway,
    path: tuple[str, ...],
    value: object,  # type: ignore[no-untyped-def]
) -> None:
    document = deepcopy(gateway.request())
    target = document
    for key in path[:-1]:
        target = target[key]  # type: ignore[assignment,index]
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValidationError):
        InferenceRequest.model_validate(document)


def test_contract_rejects_partial_message_shape(gateway) -> None:  # type: ignore[no-untyped-def]
    document = gateway.request()
    document["generation"]["messages"] = [  # type: ignore[index]
        {"role": "user", "content": "missing system message"}
    ]

    with pytest.raises(ValidationError):
        InferenceRequest.model_validate(document)


def test_contract_bounds_schema_depth_and_request_json(gateway) -> None:  # type: ignore[no-untyped-def]
    document = gateway.request()
    nested: dict[str, object] = {}
    cursor = nested
    for _ in range(40):
        child: dict[str, object] = {}
        cursor["child"] = child
        cursor = child
    document["generation"]["response_schema"] = nested  # type: ignore[index]

    with pytest.raises(ValidationError, match="depth"):
        InferenceRequest.model_validate(document)
    with pytest.raises(ValueError, match="JSON object"):
        parse_json_object(b"[]")


@pytest.mark.parametrize("number", [b"NaN", b"1e999"])
def test_contract_rejects_non_finite_json(gateway, number: bytes) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_json_object(b'{"response_schema":{"minimum":' + number + b"}}")

    document = gateway.request()
    document["generation"]["response_schema"] = {"minimum": float("inf")}  # type: ignore[index]
    with pytest.raises(ValidationError):
        InferenceRequest.model_validate(document)


def test_contract_rejects_integer_tokens_outside_the_supported_finite_range() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_json_object(b'{"response_schema":{"enum":[' + b"9" * 400 + b"]}}")


def test_contract_rejects_duplicate_keys_and_fractional_schema_bounds(gateway) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_json_object(b'{"request_id":"first","request_id":"second"}')

    document = gateway.request()
    document["generation"]["response_schema"] = {  # type: ignore[index]
        "type": "object",
        "properties": {"confidence": {"type": "number", "maximum": 0.5}},
    }
    with pytest.raises(ValidationError, match="numeric bounds must be integers"):
        InferenceRequest.model_validate(document)


def test_contract_rejects_invalid_or_referencing_response_schema(gateway) -> None:  # type: ignore[no-untyped-def]
    invalid = gateway.request()
    invalid["generation"]["response_schema"] = {"type": 42}  # type: ignore[index]
    reference = gateway.request()
    reference["generation"]["response_schema"] = {"$ref": "#/missing"}  # type: ignore[index]

    with pytest.raises(ValidationError, match="unsupported type"):
        InferenceRequest.model_validate(invalid)
    with pytest.raises(ValidationError, match="unsupported keyword"):
        InferenceRequest.model_validate(reference)


def test_contract_allows_only_bounded_nullable_schema_composition(gateway) -> None:  # type: ignore[no-untyped-def]
    accepted = gateway.request()
    accepted["generation"]["response_schema"] = {  # type: ignore[index]
        "type": "object",
        "properties": {
            "summary": {
                "anyOf": [
                    {"type": "string", "maxLength": 800},
                    {"type": "null"},
                ]
            }
        },
        "required": ["summary"],
        "additionalProperties": False,
    }
    hostile = gateway.request()
    hostile["generation"]["response_schema"] = {  # type: ignore[index]
        "type": "object",
        "properties": {"summary": {"type": "string", "pattern": "(a+)+$"}},
    }
    non_scalar_enum = gateway.request()
    non_scalar_enum["generation"]["response_schema"] = {  # type: ignore[index]
        "type": "object",
        "enum": [{"nested": "value"}],
    }

    InferenceRequest.model_validate(accepted)
    with pytest.raises(ValidationError, match="unsupported keyword"):
        InferenceRequest.model_validate(hostile)
    with pytest.raises(ValidationError, match="bounded scalar"):
        InferenceRequest.model_validate(non_scalar_enum)


@pytest.mark.parametrize("max_items", [0, 100])
def test_contract_accepts_bounded_array_limits(gateway, max_items: int) -> None:  # type: ignore[no-untyped-def]
    document = gateway.request()
    document["generation"]["response_schema"] = {  # type: ignore[index]
        "type": "object",
        "properties": {
            "values": {
                "type": "array",
                "items": {"type": "string", "maxLength": 20},
                "minItems": 0,
                "maxItems": max_items,
            }
        },
    }

    InferenceRequest.model_validate(document)


@pytest.mark.parametrize(
    ("array_schema", "message"),
    [
        ({"type": "array", "maxItems": 1}, "one item schema"),
        ({"type": "array", "items": [{"type": "string"}], "maxItems": 1}, "one item schema"),
        ({"type": "array", "items": {"type": "string"}}, "bounded maxItems"),
        ({"type": "array", "items": {"type": "string"}, "maxItems": True}, "bounded maxItems"),
        ({"type": "array", "items": {"type": "string"}, "maxItems": 101}, "bounded maxItems"),
        (
            {"type": "array", "items": {"type": "string"}, "minItems": -1, "maxItems": 1},
            "minItems is invalid",
        ),
        (
            {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 1},
            "minItems is invalid",
        ),
        ({"type": "string", "items": {"type": "string"}, "maxItems": 1}, "require array"),
    ],
)
def test_contract_rejects_unbounded_or_malformed_arrays(
    gateway,
    array_schema: dict[str, object],
    message: str,  # type: ignore[no-untyped-def]
) -> None:
    document = gateway.request()
    document["generation"]["response_schema"] = {  # type: ignore[index]
        "type": "object",
        "properties": {"values": array_schema},
    }

    with pytest.raises(ValidationError, match=message):
        InferenceRequest.model_validate(document)


@pytest.mark.parametrize("container_type", ["array", "object"])
def test_contract_array_or_object_recursion_cannot_reset_nullable_union_guard(
    gateway,
    container_type: str,  # type: ignore[no-untyped-def]
) -> None:
    nested_nullable = {
        "anyOf": [
            {"type": "string", "maxLength": 20},
            {"type": "null"},
        ]
    }
    if container_type == "array":
        non_null_branch = {
            "type": "array",
            "items": nested_nullable,
            "maxItems": 4,
        }
    else:
        non_null_branch = {
            "type": "object",
            "properties": {"nested": nested_nullable},
        }
    document = gateway.request()
    document["generation"]["response_schema"] = {  # type: ignore[index]
        "type": "object",
        "properties": {
            "value": {
                "anyOf": [
                    non_null_branch,
                    {"type": "null"},
                ]
            }
        },
    }

    with pytest.raises(ValidationError, match="one bounded nullable union"):
        InferenceRequest.model_validate(document)


@pytest.mark.parametrize("schema_type", [["string", "null"]])
def test_contract_rejects_unsupported_nested_schema_types(
    gateway,
    schema_type: object,  # type: ignore[no-untyped-def]
) -> None:
    document = gateway.request()
    document["generation"]["response_schema"] = {  # type: ignore[index]
        "type": "object",
        "properties": {"value": {"type": schema_type}},
    }

    with pytest.raises(ValidationError, match="unsupported type"):
        InferenceRequest.model_validate(document)
