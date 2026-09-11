from __future__ import annotations

import base64
import hashlib
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_inference_gateway.app import create_app
from local_inference_gateway.config import Credential, CredentialStore, Settings
from local_inference_gateway.store import RequestStore, ResultCipher
from local_inference_gateway.worker import WorkerResult

TOKEN = "email-watcher-test-token-000000000000"
OTHER_TOKEN = "document-summarizer-token-0000000000"
REQUEST_ID = "12345678-1234-4234-8234-123456789abc"


class ManualClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class FakeWorker:
    def __init__(self) -> None:
        self.available = True
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.error: Exception | None = None
        self.before_return = None
        self.result_content = '{"summary":"private generated result"}'

    def health(self) -> bool:
        return self.available

    def infer(self, request, timeout_seconds: float) -> WorkerResult:  # type: ignore[no-untyped-def]
        del request, timeout_seconds
        self.calls += 1
        self.started.set()
        if not self.release.wait(5):
            raise RuntimeError("test worker was not released")
        if self.before_return is not None:
            self.before_return()
        if self.error is not None:
            raise self.error
        return WorkerResult("application/json", self.result_content)


@dataclass
class GatewayHarness:
    settings: Settings
    credentials: CredentialStore
    store: RequestStore
    worker: FakeWorker
    clock: ManualClock
    client: TestClient

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {TOKEN}"}

    @property
    def other_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {OTHER_TOKEN}"}

    def request(self, **overrides: object) -> dict[str, object]:
        document: dict[str, object] = {
            "protocol_version": 1,
            "request_id": REQUEST_ID,
            "request_expires_at": (self.clock() + timedelta(minutes=5)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "task": {"id": "email.analyze", "version": 1},
            "requirements": {
                "input_modalities": ["text"],
                "output_media_type": "application/json",
                "structured_output": True,
                "max_output_tokens": 500,
            },
            "generation": {
                "messages": [
                    {"role": "system", "content": "trusted application prompt"},
                    {"role": "user", "content": "private email body"},
                ],
                "temperature": 0.1,
                "response_schema": {"type": "object"},
            },
        }
        document.update(overrides)
        return document


def credential_store() -> CredentialStore:
    email = Credential(
        hashlib.sha256(b"email-watcher").hexdigest(),
        hashlib.sha256(TOKEN.encode("ascii")).hexdigest(),
        frozenset({("email.analyze", 1), ("email.schedule.extract", 1)}),
    )
    other = Credential(
        hashlib.sha256(b"document-summarizer").hexdigest(),
        hashlib.sha256(OTHER_TOKEN.encode("ascii")).hexdigest(),
        frozenset(
            {
                ("document.chunk.summarize", 1),
                ("document.summary.step", 1),
            }
        ),
    )
    return CredentialStore((email, other))


def build_harness(
    tmp_path: Path,
    *,
    max_open_total: int = 16,
    max_open_per_credential: int = 4,
    request_max_bytes: int = 1_000_000,
    maintenance_interval_seconds: float = 30.0,
    database_name: str = "gateway.sqlite3",
    clock: ManualClock | None = None,
    worker: FakeWorker | None = None,
) -> GatewayHarness:
    tmp_path.mkdir(parents=True, exist_ok=True)
    clock = clock or ManualClock()
    worker = worker or FakeWorker()
    key_path = tmp_path / "result-key"
    key_path.write_bytes(base64.urlsafe_b64encode(b"k" * 32))
    key_path.chmod(0o600)
    settings = Settings(
        database_path=tmp_path / database_name,
        credentials_path=tmp_path / "credentials.json",
        encryption_key_path=key_path,
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="qwen3-30b-a3b:latest",
        deployment_id="test-deployment",
        request_max_bytes=request_max_bytes,
        maintenance_interval_seconds=maintenance_interval_seconds,
        max_open_total=max_open_total,
        max_open_per_credential=max_open_per_credential,
    )
    store = RequestStore(
        settings.database_path,
        ResultCipher.from_file(key_path),
        max_open_total=max_open_total,
        max_open_per_credential=max_open_per_credential,
        tombstone_retention_seconds=settings.tombstone_retention_seconds,
    )
    credentials = credential_store()
    app = create_app(
        settings,
        credentials=credentials,
        store=store,
        worker=worker,  # type: ignore[arg-type]
        clock=clock,
    )
    return GatewayHarness(settings, credentials, store, worker, clock, TestClient(app))


@pytest.fixture
def gateway(tmp_path: Path) -> GatewayHarness:
    return build_harness(tmp_path)
