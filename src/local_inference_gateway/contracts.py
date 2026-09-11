from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 1_000_000
MAX_MESSAGE_CHARS = 500_000
MAX_SCHEMA_BYTES = 250_000
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 20_000
MAX_SCHEMA_ENUM_VALUES = 100
SUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "additionalProperties",
        "anyOf",
        "default",
        "enum",
        "maxLength",
        "maximum",
        "minLength",
        "minimum",
        "properties",
        "required",
        "title",
        "type",
    }
)
UUID_V4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
RFC3339_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _validate_protocol_version(value: int) -> int:
    if type(value) is not int or value != PROTOCOL_VERSION:
        raise ValueError(f"protocol_version must be exactly {PROTOCOL_VERSION}")
    return value


def _validate_request_id(value: str) -> str:
    if UUID_V4_RE.fullmatch(value) is None:
        raise ValueError("request_id must be canonical lowercase UUIDv4 text")
    return value


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TaskReference(ContractModel):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
    version: int = Field(ge=1, le=2_147_483_647)


class Requirements(ContractModel):
    input_modalities: list[Literal["text"]] = Field(min_length=1, max_length=1)
    output_media_type: Literal["application/json"]
    structured_output: Literal[True]
    max_output_tokens: int = Field(ge=1, le=1_500)

    @field_validator("input_modalities")
    @classmethod
    def require_unique_modalities(cls, value: list[Literal["text"]]) -> list[Literal["text"]]:
        if value != ["text"]:
            raise ValueError("input_modalities must be exactly ['text']")
        return value


class GenerationMessage(ContractModel):
    role: Literal["system", "user"]
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class Generation(ContractModel):
    messages: list[GenerationMessage] = Field(min_length=2, max_length=2)
    temperature: float = Field(ge=0.0, le=1.0)
    response_schema: dict[str, Any]

    @model_validator(mode="after")
    def validate_generation_shape(self) -> Generation:
        if [message.role for message in self.messages] != ["system", "user"]:
            raise ValueError(
                "messages must contain one system message followed by one user message"
            )
        schema_bytes = json.dumps(
            self.response_schema,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        if len(schema_bytes) > MAX_SCHEMA_BYTES:
            raise ValueError("response_schema exceeds its byte limit")
        _validate_json_shape(self.response_schema)
        _validate_supported_schema(self.response_schema)
        try:
            Draft202012Validator.check_schema(self.response_schema)
        except SchemaError as exc:
            raise ValueError("response_schema is not valid JSON Schema") from exc
        if self.response_schema.get("type") != "object":
            raise ValueError("response_schema root type must be object")
        return self


class InferenceRequest(ContractModel):
    protocol_version: int
    request_id: str
    request_expires_at: str
    task: TaskReference
    requirements: Requirements
    generation: Generation

    @field_validator("protocol_version")
    @classmethod
    def validate_protocol_version(cls, value: int) -> int:
        return _validate_protocol_version(value)

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str) -> str:
        return _validate_request_id(value)

    @field_validator("request_expires_at")
    @classmethod
    def validate_request_expiry(cls, value: str) -> str:
        if RFC3339_UTC_RE.fullmatch(value) is None:
            raise ValueError("request_expires_at must be whole-second RFC3339 UTC text")
        try:
            datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise ValueError("request_expires_at must be a valid timestamp") from exc
        return value

    @property
    def expires_at(self) -> datetime:
        return datetime.strptime(self.request_expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

    def canonical_digest(self) -> str:
        encoded = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class AcknowledgementRequest(ContractModel):
    protocol_version: int
    request_id: str
    disposition: Literal["persisted", "application_rejected"]

    @field_validator("protocol_version")
    @classmethod
    def validate_protocol_version(cls, value: int) -> int:
        return _validate_protocol_version(value)

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str) -> str:
        return _validate_request_id(value)


def _validate_json_shape(value: object) -> None:
    stack: list[tuple[object, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise ValueError("response_schema exceeds its node limit")
        if depth > MAX_JSON_DEPTH:
            raise ValueError("response_schema exceeds its depth limit")
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


def _validate_supported_schema(
    schema: dict[str, Any], *, allow_nullable_any_of: bool = True
) -> None:
    unsupported = set(schema) - SUPPORTED_SCHEMA_KEYWORDS
    if unsupported:
        raise ValueError(f"response_schema uses unsupported keyword {sorted(unsupported)[0]!r}")
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for child in properties.values():
            if not isinstance(child, dict):
                raise ValueError("response_schema properties must contain schemas")
            _validate_supported_schema(child)
    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        raise ValueError("response_schema additionalProperties must be boolean")
    enum = schema.get("enum")
    if enum is not None and (
        not isinstance(enum, list)
        or not 1 <= len(enum) <= MAX_SCHEMA_ENUM_VALUES
        or any(isinstance(item, (dict, list)) for item in enum)
    ):
        raise ValueError("response_schema enum must contain bounded scalar values")
    alternatives = schema.get("anyOf")
    if alternatives is None:
        return
    if not allow_nullable_any_of or not isinstance(alternatives, list) or len(alternatives) != 2:
        raise ValueError("response_schema anyOf must be one bounded nullable union")
    null_branches = [item for item in alternatives if item == {"type": "null"}]
    if len(null_branches) != 1:
        raise ValueError("response_schema anyOf must contain one null branch")
    non_null_branch = next(item for item in alternatives if item != {"type": "null"})
    branch_type = non_null_branch.get("type") if isinstance(non_null_branch, dict) else None
    if not isinstance(branch_type, str) or branch_type == "null":
        raise ValueError("response_schema anyOf must contain one typed non-null branch")
    for child in alternatives:
        if not isinstance(child, dict):
            raise ValueError("response_schema anyOf must contain schemas")
        _validate_supported_schema(child, allow_nullable_any_of=False)


def parse_json_object(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            raw,
            parse_constant=_reject_json_constant,
            parse_float=parse_json_float,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ValueError("request body is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("request body must be a JSON object")
    _validate_json_shape(value)
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value} is not supported")


def parse_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number is not supported")
    return parsed


def safe_request_id(value: object) -> str | None:
    return value if isinstance(value, str) and UUID_V4_RE.fullmatch(value) else None
