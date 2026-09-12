#!/usr/bin/env python3
"""Exercise a loopback HTTPS gateway with synthetic application requests."""

from __future__ import annotations

import argparse
import json
import os
import stat
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

TASKS = {
    "email.analyze": (0.1, "classification", "synthetic"),
    "email.schedule.extract": (0.1, "intent", "none"),
    "document.summary.step": (0.0, "summary", "synthetic"),
}


class ProofError(RuntimeError):
    """The operational proof did not establish its contract."""


def _origin(value: str) -> str:
    try:
        parsed = urlsplit(value.rstrip("/"))
        address = ip_address(parsed.hostname or "")
        port = parsed.port
    except ValueError as exc:
        raise ProofError("base URL must be an explicit loopback HTTPS origin") from exc
    if (
        parsed.scheme != "https"
        or not address.is_loopback
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ProofError("base URL must be an explicit loopback HTTPS origin")
    return f"https://{f'[{address}]' if address.version == 6 else address}:{port}"


def _token(path: Path) -> str:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > 512
            or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077)
        ):
            raise ProofError("credential file is invalid or not owner-private")
        token = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise ProofError("credential file is unavailable or invalid") from exc
    if not 32 <= len(token) <= 512 or any(character.isspace() for character in token):
        raise ProofError("credential file is invalid")
    return token


def _request(task: str) -> dict[str, Any]:
    temperature, field, expected = TASKS[task]
    return {
        "protocol_version": 1,
        "request_id": str(uuid.uuid4()),
        "request_expires_at": (datetime.now(UTC) + timedelta(minutes=10)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "task": {"id": task, "version": 1},
        "requirements": {
            "input_modalities": ["text"],
            "output_media_type": "application/json",
            "structured_output": True,
            "max_output_tokens": 64,
        },
        "generation": {
            "messages": [
                {"role": "system", "content": f"Return only JSON; set {field} to {expected}."},
                {"role": "user", "content": "Synthetic operational transport proof."},
            ],
            "temperature": temperature,
            "seed": 7,
            "response_schema": {
                "type": "object",
                "properties": {field: {"type": "string", "enum": [expected]}},
                "required": [field],
                "additionalProperties": False,
            },
        },
    }


def _json(response: httpx.Response) -> dict[str, Any]:
    if response.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        raise ProofError("gateway response media type is invalid")
    try:
        document = response.json()
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ProofError("gateway response is invalid JSON") from exc
    if not isinstance(document, dict):
        raise ProofError("gateway response must be an object")
    return document


def _emit(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, sort_keys=True, separators=(",", ":")))


class Proof:
    def __init__(
        self,
        base_url: str,
        ca_file: Path,
        email_token_file: Path,
        document_token_file: Path,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self.email_token = _token(email_token_file)
        self.document_token = _token(document_token_file)
        if self.email_token == self.document_token:
            raise ProofError("application credentials must be distinct")
        self.client = httpx.Client(
            base_url=_origin(base_url),
            verify=str(ca_file),
            timeout=300,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def call(
        self, method: str, path: str, token: str | None = None, body: object | None = None
    ) -> tuple[int, dict[str, Any]]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = self.client.request(method, path, headers=headers, json=body)
        return response.status_code, _json(response)

    def health(self, *, worker_available: bool = True) -> None:
        status, live = self.call("GET", "/health/live")
        if status != 200 or live != {"protocol_version": 1, "status": "live"}:
            raise ProofError("liveness response is incompatible")
        task_status = "available" if worker_available else "unavailable"
        diagnostic_code = "ready" if worker_available else "worker_unavailable"
        expected = (
            (
                self.email_token,
                [
                    {
                        "id": "email.analyze",
                        "version": 1,
                        "status": task_status,
                        "diagnostic_code": diagnostic_code,
                    },
                    {
                        "id": "email.schedule.extract",
                        "version": 1,
                        "status": task_status,
                        "diagnostic_code": diagnostic_code,
                    },
                ],
            ),
            (
                self.document_token,
                [
                    {
                        "id": "document.summary.step",
                        "version": 1,
                        "status": task_status,
                        "diagnostic_code": diagnostic_code,
                    }
                ],
            ),
        )
        for token, tasks in expected:
            status, health = self.call("GET", "/v1/health", token)
            if status != 200 or health != {"protocol_version": 1, "tasks": tasks}:
                raise ProofError("credential-scoped health is incompatible")
        _emit("health", status="available" if worker_available else "worker_unavailable")

    def completed(self, token: str, request: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        status, response = self.call("POST", "/v1/inference", token, request)
        output = response.get("output")
        if (
            status != 200
            or response.get("protocol_version") != 1
            or response.get("request_id") != request["request_id"]
            or response.get("status") != "completed"
            or not isinstance(output, dict)
        ):
            raise ProofError("inference did not complete")
        schema = request["generation"]["response_schema"]
        field = schema["required"][0]
        expected = schema["properties"][field]["enum"][0]
        try:
            generated = json.loads(output.get("content", ""))
        except (TypeError, ValueError) as exc:
            raise ProofError("inference output is invalid") from exc
        if output.get("media_type") != "application/json" or generated != {field: expected}:
            raise ProofError("inference output is invalid")
        _emit(
            "inference",
            task=request["task"]["id"],
            request_id=request["request_id"],
            status="completed",
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
        return response

    def acknowledge(self, token: str, request: dict[str, Any]) -> None:
        status, response = self.call(
            "POST",
            f"/v1/inference/{request['request_id']}/ack",
            token,
            {
                "protocol_version": 1,
                "request_id": request["request_id"],
                "disposition": "persisted",
            },
        )
        expected = {
            "protocol_version": 1,
            "request_id": request["request_id"],
            "status": "acknowledged",
            "disposition": "persisted",
        }
        if status != 200 or response != expected:
            raise ProofError("acknowledgement failed")
        status, response = self.call("POST", "/v1/inference", token, request)
        expected = {
            "protocol_version": 1,
            "request_id": request["request_id"],
            "status": "failed",
            "error": {"code": "unknown_request", "retryable": False},
        }
        if status != 410 or response != expected:
            raise ProofError("acknowledgement cleanup failed")

    def run(self) -> None:
        self.health()
        requests = (
            (self.email_token, "email.analyze"),
            (self.email_token, "email.schedule.extract"),
            (self.document_token, "document.summary.step"),
        )
        forbidden = (
            (self.email_token, "document.summary.step"),
            (self.document_token, "email.analyze"),
            (self.document_token, "email.schedule.extract"),
        )
        for token, task in forbidden:
            status, response = self.call("POST", "/v1/inference", token, _request(task))
            error = response.get("error")
            if status != 403 or not isinstance(error, dict) or error.get("code") != "forbidden":
                raise ProofError("cross-credential task access did not fail closed")
        _emit("authorization", status="forbidden")
        for token, task in requests:
            request = _request(task)
            first = self.completed(token, request)
            if self.completed(token, request) != first:
                raise ProofError("exact replay changed the result")
            self.acknowledge(token, request)
            _emit("acknowledgement", task=request["task"]["id"], status="acknowledged")


def _write_state(path: Path, request: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix" and stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
        raise ProofError("restart state directory must be owner-private")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(request, stream, sort_keys=True, separators=(",", ":"))
    except FileExistsError as exc:
        raise ProofError("restart state file already exists") from exc


def _read_state(path: Path) -> dict[str, Any]:
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ProofError("restart state file must be owner-private")
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise ProofError("restart state is invalid") from exc
    if not isinstance(request, dict) or request.get("task") != {
        "id": "document.summary.step",
        "version": 1,
    }:
        raise ProofError("restart state is invalid")
    return request


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("run", "prepare-restart", "reconcile-restart"))
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--ca-file", required=True, type=Path)
    parser.add_argument("--email-token-file", required=True, type=Path)
    parser.add_argument("--document-token-file", required=True, type=Path)
    parser.add_argument("--restart-state-file", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.phase != "run" and arguments.restart_state_file is None:
        raise ProofError("restart phases require --restart-state-file")
    proof = Proof(
        arguments.base_url,
        arguments.ca_file,
        arguments.email_token_file,
        arguments.document_token_file,
    )
    try:
        if arguments.phase == "run":
            proof.run()
        elif arguments.phase == "prepare-restart":
            proof.health()
            request = _request("document.summary.step")
            _write_state(arguments.restart_state_file, request)
            proof.completed(proof.document_token, request)
            _emit("restart_prepare", request_id=request["request_id"], status="retained")
        else:
            proof.health(worker_available=False)
            request = _read_state(arguments.restart_state_file)
            proof.completed(proof.document_token, request)
            proof.acknowledge(proof.document_token, request)
            _emit("restart_reconcile", request_id=request["request_id"], status="acknowledged")
    finally:
        proof.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
