from __future__ import annotations

import hashlib
import json
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
        _validate_schema_references(self.response_schema)
        try:
            Draft202012Validator.check_schema(self.response_schema)
        except SchemaError as exc:
            raise ValueError("response_schema is not valid JSON Schema") from exc
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


def _validate_schema_references(schema: dict[str, Any]) -> None:
    stack: list[object] = [schema]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key in {"$ref", "$dynamicRef"} and (
                    not isinstance(value, str) or not value.startswith("#")
                ):
                    raise ValueError("response_schema references must stay within the request")
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)


def parse_json_object(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(raw, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ValueError("request body is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("request body must be a JSON object")
    _validate_json_shape(value)
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value} is not supported")


def safe_request_id(value: object) -> str | None:
    return value if isinstance(value, str) and UUID_V4_RE.fullmatch(value) else None
