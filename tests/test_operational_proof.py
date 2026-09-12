from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest


def load_module():  # type: ignore[no-untyped-def]
    path = Path(__file__).parents[1] / "scripts" / "prove_operational_gateway.py"
    spec = importlib.util.spec_from_file_location("gateway_proof", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path, Path]:
    ca = tmp_path / "ca.pem"
    ca.write_text("test transport does not load this CA", encoding="ascii")
    email = tmp_path / "email.token"
    email.write_text("e" * 32, encoding="ascii")
    email.chmod(0o600)
    document = tmp_path / "document.token"
    document.write_text("d" * 32, encoding="ascii")
    document.chmod(0o600)
    return ca, email, document


class Fixture:
    def __init__(self) -> None:
        self.requests: dict[str, tuple[object, object]] = {}
        self.submissions: list[dict[str, object]] = []
        self.forbidden_submissions: list[tuple[str, str]] = []
        self.extra_health_task: dict[str, object] | None = None
        self.health_extra_fields: dict[str, object] = {}
        self.task_extra_fields: dict[str, object] = {}
        self.inference_protocol_version = 1
        self.inference_request_id: str | None = None
        self.omit_inference_request_id = False
        self.ack_protocol_version = 1
        self.ack_request_id: str | None = None
        self.retain_after_ack = False
        self.acknowledged: set[str] = set()
        self.events: list[tuple[str, str]] = []
        self.worker_available = True
        self.forbidden_extra_fields: dict[str, object] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if request.url.path == "/health/live":
            return self.response({"protocol_version": 1, "status": "live"})
        if request.url.path == "/v1/health":
            status = "available" if self.worker_available else "unavailable"
            diagnostic_code = "ready" if self.worker_available else "worker_unavailable"
            tasks = (
                [
                    {
                        "id": "email.analyze",
                        "version": 1,
                        "status": status,
                        "diagnostic_code": diagnostic_code,
                    },
                    {
                        "id": "email.schedule.extract",
                        "version": 1,
                        "status": status,
                        "diagnostic_code": diagnostic_code,
                    },
                ]
                if token == "e" * 32
                else [
                    {
                        "id": "document.summary.step",
                        "version": 1,
                        "status": status,
                        "diagnostic_code": diagnostic_code,
                    }
                ]
            )
            if self.extra_health_task is not None:
                tasks.append(self.extra_health_task)
            for task in tasks:
                task.update(self.task_extra_fields)
            return self.response(
                {"protocol_version": 1, "tasks": tasks, **self.health_extra_fields}
            )
        body = json.loads(request.content)
        if request.url.path == "/v1/inference":
            self.submissions.append(body)
            task = body["task"]["id"]
            self.events.append(("submitted", task))
            authorized = {
                "e" * 32: {"email.analyze", "email.schedule.extract"},
                "d" * 32: {"document.summary.step"},
            }
            if task not in authorized.get(token, set()):
                self.forbidden_submissions.append((token, task))
                return self.response(
                    {
                        "protocol_version": 1,
                        "request_id": body["request_id"],
                        "status": "failed",
                        "error": {"code": "forbidden", "retryable": False},
                        **self.forbidden_extra_fields,
                    },
                    403,
                )
            if body["request_id"] in self.acknowledged:
                return self.response(
                    {
                        "protocol_version": 1,
                        "request_id": body["request_id"],
                        "status": "failed",
                        "error": {"code": "unknown_request", "retryable": False},
                    },
                    410,
                )
            original = self.requests.get(body["request_id"])
            if original is not None:
                assert original[0] == body
                return self.response(original[1])
            if not self.worker_available:
                return self.response(
                    {
                        "protocol_version": 1,
                        "request_id": body["request_id"],
                        "status": "failed",
                        "error": {"code": "worker_unavailable", "retryable": True},
                    },
                    503,
                )
            schema = body["generation"]["response_schema"]
            field = schema["required"][0]
            expected = schema["properties"][field]["enum"][0]
            result = {
                "protocol_version": self.inference_protocol_version,
                "status": "completed",
                "output": {
                    "media_type": "application/json",
                    "content": json.dumps({field: expected}),
                },
            }
            if not self.omit_inference_request_id:
                result["request_id"] = self.inference_request_id or body["request_id"]
            self.requests[body["request_id"]] = (body, result)
            return self.response(result)
        if request.url.path.endswith("/ack"):
            request_id = body["request_id"]
            if not self.retain_after_ack:
                self.acknowledged.add(request_id)
            return self.response(
                {
                    "protocol_version": self.ack_protocol_version,
                    "request_id": self.ack_request_id or request_id,
                    "status": "acknowledged",
                    "disposition": "persisted",
                }
            )
        raise AssertionError(request.url.path)

    @staticmethod
    def response(document: object, status: int = 200) -> httpx.Response:
        return httpx.Response(
            status,
            content=json.dumps(document).encode(),
            headers={"content-type": "application/json"},
        )


def proof(module, files, fixture):  # type: ignore[no-untyped-def]
    return module.Proof("https://127.0.0.1:8787", *files, transport=httpx.MockTransport(fixture))


def test_full_proof_scopes_replays_acknowledges_and_redacts(
    files: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_module()
    fixture = Fixture()
    client = proof(module, files, fixture)
    try:
        client.run()
    finally:
        client.close()
    output = capsys.readouterr().out
    assert {item["task"]["id"] for item in fixture.submissions} == set(module.TASKS)
    assert set(fixture.forbidden_submissions) == {
        ("e" * 32, "document.summary.step"),
        ("d" * 32, "email.analyze"),
        ("d" * 32, "email.schedule.extract"),
    }
    assert "Synthetic operational transport proof" not in output
    assert "e" * 32 not in output and "d" * 32 not in output
    assert "synthetic" not in output


def test_restart_state_reuses_one_request(files: tuple[Path, Path, Path], tmp_path: Path) -> None:
    module = load_module()
    fixture = Fixture()
    state = tmp_path / "private" / "request.json"
    request = module._request("document.summary.step")
    module._write_state(state, request)
    first = proof(module, files, fixture)
    first.completed(first.document_token, request)
    first.close()
    fixture.worker_available = False
    second = proof(module, files, fixture)
    second.health(worker_available=False)
    second.completed(second.document_token, module._read_state(state))
    second.acknowledge(second.document_token, request)
    second.close()
    matching = [item for item in fixture.submissions if item["request_id"] == request["request_id"]]
    assert matching == [request, request, request]
    assert state.stat().st_mode & 0o777 == 0o600


def test_unavailable_worker_cannot_execute_missing_restart_state(
    files: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_module()
    fixture = Fixture()
    fixture.worker_available = False
    client = proof(module, files, fixture)
    try:
        client.health(worker_available=False)
        assert json.loads(capsys.readouterr().out)["status"] == "worker_unavailable"
        with pytest.raises(module.ProofError, match="inference did not complete"):
            client.completed(client.document_token, module._request("document.summary.step"))
    finally:
        client.close()


def test_restart_request_can_use_full_supported_lifetime() -> None:
    module = load_module()
    before = datetime.now(UTC) + timedelta(seconds=895)
    request = module._request("document.summary.step", lifetime_seconds=900)
    expires_at = datetime.strptime(request["request_expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=UTC
    )
    assert expires_at >= before


def test_proof_rejects_unsafe_endpoint_and_credential(files: tuple[Path, Path, Path]) -> None:
    module = load_module()
    ca, email, document = files
    with pytest.raises(module.ProofError, match="loopback HTTPS"):
        module.Proof("http://127.0.0.1:8787", ca, email, document)
    with pytest.raises(module.ProofError, match="loopback HTTPS"):
        module.Proof("https://192.168.1.20:8787", ca, email, document)
    email.chmod(0o644)
    with pytest.raises(module.ProofError, match="owner-private"):
        module.Proof("https://127.0.0.1:8787", ca, email, document)


def test_health_rejects_extra_cross_scope_task(files: tuple[Path, Path, Path]) -> None:
    module = load_module()
    fixture = Fixture()
    fixture.extra_health_task = {
        "id": "document.summary.step",
        "version": 1,
        "status": "unavailable",
    }
    client = proof(module, files, fixture)
    try:
        with pytest.raises(module.ProofError, match="credential-scoped health"):
            client.health()
    finally:
        client.close()


@pytest.mark.parametrize("location", ["health", "task"])
def test_health_rejects_undeclared_metadata(location: str, files: tuple[Path, Path, Path]) -> None:
    module = load_module()
    fixture = Fixture()
    if location == "health":
        fixture.health_extra_fields["model"] = "must-not-leak"
    else:
        fixture.task_extra_fields["credential"] = "must-not-leak"
    client = proof(module, files, fixture)
    try:
        with pytest.raises(module.ProofError, match="credential-scoped health"):
            client.health()
    finally:
        client.close()


@pytest.mark.parametrize("field", ["protocol_version", "request_id", "missing_request_id"])
def test_completed_rejects_response_identity_mismatch(
    field: str, files: tuple[Path, Path, Path]
) -> None:
    module = load_module()
    fixture = Fixture()
    if field == "protocol_version":
        fixture.inference_protocol_version = 2
    elif field == "missing_request_id":
        fixture.omit_inference_request_id = True
    else:
        fixture.inference_request_id = "12345678-1234-4234-8234-123456789abc"
    client = proof(module, files, fixture)
    try:
        with pytest.raises(module.ProofError, match="inference did not complete"):
            client.completed(client.email_token, module._request("email.analyze"))
    finally:
        client.close()


def test_acknowledge_rejects_response_identity_mismatch(files: tuple[Path, Path, Path]) -> None:
    module = load_module()
    fixture = Fixture()
    fixture.ack_request_id = "12345678-1234-4234-8234-123456789abc"
    client = proof(module, files, fixture)
    try:
        with pytest.raises(module.ProofError, match="acknowledgement failed"):
            client.acknowledge(client.email_token, module._request("email.analyze"))
    finally:
        client.close()


def test_acknowledge_requires_result_to_be_unreplayable(files: tuple[Path, Path, Path]) -> None:
    module = load_module()
    fixture = Fixture()
    fixture.retain_after_ack = True
    client = proof(module, files, fixture)
    request = module._request("email.analyze")
    try:
        client.completed(client.email_token, request)
        with pytest.raises(module.ProofError, match="acknowledgement finalization"):
            client.acknowledge(client.email_token, request)
    finally:
        client.close()


def test_requests_are_created_immediately_before_first_submission(
    files: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_module()
    fixture = Fixture()
    original_request = module._request

    def tracked_request(task: str):  # type: ignore[no-untyped-def]
        fixture.events.append(("created", task))
        return original_request(task)

    monkeypatch.setattr(module, "_request", tracked_request)
    client = proof(module, files, fixture)
    try:
        client.run()
    finally:
        client.close()
    for index, event in enumerate(fixture.events):
        if event[0] == "created":
            assert fixture.events[index + 1] == ("submitted", event[1])


def test_cross_credential_denial_rejects_noncanonical_envelope(
    files: tuple[Path, Path, Path],
) -> None:
    module = load_module()
    fixture = Fixture()
    fixture.forbidden_extra_fields["output"] = {"content": "must-not-appear"}
    client = proof(module, files, fixture)
    try:
        with pytest.raises(module.ProofError, match="cross-credential task access"):
            client.run()
    finally:
        client.close()
