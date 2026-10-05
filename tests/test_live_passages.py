from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from local_inference_gateway.app import create_app
from local_inference_gateway.config import Credential, CredentialStore, Settings
from local_inference_gateway.contracts import InferenceRequest, decoder_schema, encode_json_bytes
from local_inference_gateway.store import RequestStore, ResultCipher
from local_inference_gateway.worker import OllamaWorker, WorkerResult

pytestmark = pytest.mark.live


class RetainedStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, path: Path):
        self.stream = stream
        self.path = path

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        with self.path.open("xb") as output:
            async for chunk in self.stream:
                output.write(chunk)
                yield chunk

    async def aclose(self) -> None:
        await self.stream.aclose()


class RetainedTransport(httpx.AsyncBaseTransport):
    def __init__(self, output: Path, ordinal: int):
        self.inner = httpx.AsyncHTTPTransport()
        self.output = output
        self.ordinal = ordinal

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        inference = request.url.path == "/v1/chat/completions"
        if inference:
            (self.output / f"worker-request-{self.ordinal}.json").write_bytes(request.content)
        response = await self.inner.handle_async_request(request)
        if not inference:
            return response
        assert isinstance(response.stream, httpx.AsyncByteStream)
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            stream=RetainedStream(
                response.stream, self.output / f"worker-response-{self.ordinal}.json"
            ),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


class RetainedWorker(OllamaWorker):
    def __init__(self, url: str, model: str, output: Path):
        super().__init__(url, model)
        self.output = output
        self.calls = 0

    def infer(
        self,
        request: InferenceRequest,
        timeout_seconds: float,
        *,
        deadline: float | None = None,
    ) -> WorkerResult:
        self.calls += 1
        return super().infer(request, timeout_seconds, deadline=deadline)

    def _client(self, timeout_seconds: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=RetainedTransport(self.output, self.calls),
            timeout=timeout_seconds,
            trust_env=False,
            follow_redirects=False,
            headers={"Accept-Encoding": "identity"},
        )


@pytest.mark.skipif(
    os.environ.get("RUN_PASSAGE_GATEWAY_PROOF") != "1",
    reason="explicit retained requests, private output and exclusive inference lock required",
)
def test_live_passage_gateway_admission_replay_and_ack() -> None:
    os.umask(0o077)
    output = Path(os.environ["PASSAGE_PROOF_OUTPUT"])
    inputs = Path(os.environ["PASSAGE_PROOF_REQUESTS"])
    assert output.is_dir() and not (output / "results.json").exists()
    requests = sorted(inputs.glob("request-*.json"))
    assert len(requests) == 4
    key = output / "result.key"
    key.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
    settings = Settings(
        database_path=output / "gateway.sqlite3",
        credentials_path=output / "unused.json",
        encryption_key_path=key,
        ollama_base_url=os.environ["PASSAGE_PROOF_WORKER_URL"],
        ollama_model=os.environ["PASSAGE_PROOF_MODEL"],
        deployment_id="isolated-source-passage-proof",
        worker_timeout_seconds=300,
    )
    token = os.urandom(32).hex()
    credential = Credential(
        hashlib.sha256(b"passage-proof").hexdigest(),
        hashlib.sha256(token.encode()).hexdigest(),
        frozenset({("document.summary.step", 2)}),
    )
    store = RequestStore(
        settings.database_path,
        ResultCipher.from_file(key),
        max_open_total=8,
        max_open_per_credential=8,
        tombstone_retention_seconds=86_400,
    )
    worker = RetainedWorker(settings.ollama_base_url, settings.ollama_model, output)
    results: list[dict[str, Any]] = []
    with TestClient(
        create_app(settings, credentials=CredentialStore((credential,)), store=store, worker=worker)
    ) as client:
        profile = client.get(
            "/v1/tasks/document.summary.step/2/profile",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert profile.status_code == 200
        assert profile.json()["profile"]["context_tokens"] == 32768
        assert profile.json()["status"] == "available"
        (output / "task-profile.json").write_text(json.dumps(profile.json(), indent=2) + "\n")
        for path in requests:
            raw = json.loads(path.read_text())
            schema = json.loads(raw["decoder_schema_json"])
            request_id = str(uuid.uuid4())
            request = {
                "protocol_version": 1,
                "request_id": request_id,
                "request_expires_at": (datetime.now(UTC) + timedelta(minutes=10)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "task": {"id": "document.summary.step", "version": 2},
                "requirements": {
                    "input_modalities": ["text"],
                    "output_media_type": "application/json",
                    "structured_output": True,
                    "max_output_tokens": raw["max_output_tokens"],
                },
                "generation": {
                    "messages": [
                        {"role": "system", "content": raw["system_prompt"]},
                        {"role": "user", "content": raw["user_prompt"]},
                    ],
                    "temperature": 0.0,
                    "seed": raw["seed"],
                    "response_schema": schema,
                },
            }
            admitted = InferenceRequest.model_validate(request)
            assert (
                encode_json_bytes(decoder_schema(schema), sort_keys=False)
                == raw["decoder_schema_json"].encode()
            )
            headers = {"Authorization": f"Bearer {token}"}
            before = worker.calls
            response = client.post("/v1/inference", headers=headers, json=request)
            (output / f"gateway-{path.stem}.json").write_text(
                json.dumps(response.json(), indent=2) + "\n"
            )
            row = {
                "alias": path.stem,
                "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "request_id": request_id,
                "canonical_digest": admitted.canonical_digest(),
                "status": response.status_code,
                "worker_calls": worker.calls - before,
                "replay_equal": False,
                "acknowledged": False,
            }
            if response.status_code == 200:
                replay = client.post("/v1/inference", headers=headers, json=request)
                row["replay_equal"] = (
                    replay.status_code == 200
                    and replay.json() == response.json()
                    and worker.calls == before + 1
                )
                ack = client.post(
                    f"/v1/inference/{request_id}/ack",
                    headers=headers,
                    json={
                        "protocol_version": 1,
                        "request_id": request_id,
                        "disposition": "persisted",
                    },
                )
                row["acknowledged"] = ack.status_code == 200 and store.raw_persisted_values(
                    request_id
                ) == (None, None)
            row["passed"] = (
                row["status"] == 200
                and row["worker_calls"] == 1
                and row["replay_equal"]
                and row["acknowledged"]
            )
            results.append(row)
            (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
            print("PASSAGE_PROOF", json.dumps(row), flush=True)
    assert all(row["passed"] for row in results), (
        "transport proof failed; retain outputs without tuning"
    )
