from __future__ import annotations

import importlib.util
import json
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

    def __call__(self, request: httpx.Request) -> httpx.Response:
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if request.url.path == "/health/live":
            return self.response({"protocol_version": 1, "status": "live"})
        if request.url.path == "/v1/health":
            tasks = (
                [
                    {"id": "email.analyze", "version": 1, "status": "available"},
                    {"id": "email.schedule.extract", "version": 1, "status": "available"},
                ]
                if token == "e" * 32
                else [{"id": "document.summary.step", "version": 1, "status": "available"}]
            )
            return self.response({"protocol_version": 1, "tasks": tasks})
        body = json.loads(request.content)
        if request.url.path == "/v1/inference":
            self.submissions.append(body)
            if token == "e" * 32 and body["task"]["id"] == "document.summary.step":
                return self.response({"error": {"code": "forbidden"}}, 403)
            schema = body["generation"]["response_schema"]
            field = schema["required"][0]
            expected = schema["properties"][field]["enum"][0]
            result = {
                "status": "completed",
                "output": {
                    "media_type": "application/json",
                    "content": json.dumps({field: expected}),
                },
            }
            original = self.requests.setdefault(body["request_id"], (body, result))
            assert original[0] == body
            return self.response(original[1])
        if request.url.path.endswith("/ack"):
            return self.response({"status": "acknowledged"})
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
    second = proof(module, files, fixture)
    second.completed(second.document_token, module._read_state(state))
    second.acknowledge(second.document_token, request)
    second.close()
    matching = [item for item in fixture.submissions if item["request_id"] == request["request_id"]]
    assert matching == [request, request]
    assert state.stat().st_mode & 0o777 == 0o600


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
