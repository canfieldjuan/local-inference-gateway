from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_inference_gateway.app import create_app
from local_inference_gateway.config import Credential, CredentialStore, Settings
from local_inference_gateway.store import RequestStore, ResultCipher
from local_inference_gateway.worker import OllamaWorker

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.environ.get("RUN_OLLAMA_SMOKE") != "1",
    reason="set RUN_OLLAMA_SMOKE=1 to exercise the local Ollama worker",
)
def test_live_ollama_request_replay_and_acknowledgement(tmp_path: Path) -> None:
    token = "synthetic-live-smoke-token-000000000000"
    key_path = tmp_path / "result.key"
    key_path.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
    key_path.chmod(0o600)
    settings = Settings(
        database_path=tmp_path / "gateway.sqlite3",
        credentials_path=tmp_path / "unused.json",
        encryption_key_path=key_path,
        ollama_base_url=os.environ.get("GATEWAY_OLLAMA_URL", "http://127.0.0.1:11434"),
        ollama_model=os.environ.get("GATEWAY_OLLAMA_MODEL", "qwen3-30b-a3b:latest"),
        deployment_id="live-smoke",
        worker_timeout_seconds=300,
    )
    credential = Credential(
        hashlib.sha256(b"live-smoke").hexdigest(),
        hashlib.sha256(token.encode("ascii")).hexdigest(),
        frozenset({("email.analyze", 1)}),
    )
    store = RequestStore(
        settings.database_path,
        ResultCipher.from_file(key_path),
        max_open_total=2,
        max_open_per_credential=2,
        tombstone_retention_seconds=86_400,
    )
    worker = OllamaWorker(settings.ollama_base_url, settings.ollama_model)
    if not worker.health():
        pytest.fail("configured Ollama model is not available on the loopback worker")
    client = TestClient(
        create_app(
            settings,
            credentials=CredentialStore((credential,)),
            store=store,
            worker=worker,
        )
    )
    request_id = str(uuid.uuid4())
    expiry = (datetime.now(UTC) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    document = {
        "protocol_version": 1,
        "request_id": request_id,
        "request_expires_at": expiry,
        "task": {"id": "email.analyze", "version": 1},
        "requirements": {
            "input_modalities": ["text"],
            "output_media_type": "application/json",
            "structured_output": True,
            "max_output_tokens": 64,
        },
        "generation": {
            "messages": [
                {
                    "role": "system",
                    "content": "Return exactly the requested JSON classification.",
                },
                {
                    "role": "user",
                    "content": (
                        "This is synthetic transport test content. Classify it as synthetic."
                    ),
                },
            ],
            "temperature": 0.1,
            "response_schema": {
                "type": "object",
                "properties": {"classification": {"type": "string", "enum": ["synthetic"]}},
                "required": ["classification"],
                "additionalProperties": False,
            },
        },
    }
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post("/v1/inference", headers=headers, json=document)
    replay = client.post("/v1/inference", headers=headers, json=document)

    assert first.status_code == 200
    assert replay.json() == first.json()
    assert first.json()["output"]["media_type"] == "application/json"
    assert json.loads(first.json()["output"]["content"]) == {"classification": "synthetic"}

    acknowledgement = client.post(
        f"/v1/inference/{request_id}/ack",
        headers=headers,
        json={
            "protocol_version": 1,
            "request_id": request_id,
            "disposition": "persisted",
        },
    )
    assert acknowledgement.status_code == 200
    assert store.raw_persisted_values(request_id) == (None, None)
