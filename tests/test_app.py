from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

from fastapi.testclient import TestClient

from local_inference_gateway.app import create_app
from local_inference_gateway.contracts import InferenceRequest
from local_inference_gateway.store import RequestStore
from local_inference_gateway.worker import WorkerOutcomeAmbiguous, WorkerUnavailable
from tests.conftest import OTHER_TOKEN, REQUEST_ID, TOKEN, FakeWorker, build_harness


def test_liveness_and_scoped_health_disclose_no_worker_details(gateway) -> None:  # type: ignore[no-untyped-def]
    live = gateway.client.get("/health/live")
    unauthorized = gateway.client.get("/v1/health")
    health = gateway.client.get("/v1/health", headers=gateway.headers)
    other = gateway.client.get("/v1/health", headers=gateway.other_headers)

    assert live.json() == {"protocol_version": 1, "status": "live"}
    assert unauthorized.status_code == 401
    assert health.json() == {
        "protocol_version": 1,
        "tasks": [
            {
                "id": "email.analyze",
                "version": 1,
                "status": "available",
                "diagnostic_code": "ready",
            }
        ],
    }
    assert other.json() == {"protocol_version": 1, "tasks": []}
    encoded = json.dumps([live.json(), health.json(), other.json()])
    assert "ollama" not in encoded.casefold()
    assert "qwen" not in encoded.casefold()
    assert TOKEN not in encoded and OTHER_TOKEN not in encoded


def test_valid_inference_is_reserved_once_and_replayed(gateway) -> None:  # type: ignore[no-untyped-def]
    document = gateway.request()

    first = gateway.client.post("/v1/inference", headers=gateway.headers, json=document)
    repeat = gateway.client.post("/v1/inference", headers=gateway.headers, json=document)

    assert first.status_code == repeat.status_code == 200
    assert first.json() == repeat.json()
    assert first.json()["request_id"] == REQUEST_ID
    assert first.json()["output"]["content"] == '{"summary":"private generated result"}'
    assert gateway.worker.calls == 1
    database = gateway.settings.database_path.read_bytes()
    assert b"private generated result" not in database
    assert b"private email body" not in database
    assert TOKEN.encode() not in database


def test_corrupt_retained_ciphertext_returns_stable_failure(gateway) -> None:  # type: ignore[no-untyped-def]
    document = gateway.request()
    completed = gateway.client.post("/v1/inference", headers=gateway.headers, json=document)
    assert completed.status_code == 200
    connection = sqlite3.connect(gateway.settings.database_path)
    connection.execute(
        "UPDATE inference_requests SET output_ciphertext = ? WHERE request_id = ?",
        (b"corrupt", REQUEST_ID),
    )
    connection.commit()
    connection.close()

    replay = gateway.client.post("/v1/inference", headers=gateway.headers, json=document)

    assert replay.status_code == 500
    assert replay.json()["error"] == {"code": "invalid_worker_output", "retryable": False}


def test_request_identity_collision_and_owner_isolation(gateway) -> None:  # type: ignore[no-untyped-def]
    first = gateway.request()
    changed = gateway.request()
    changed["generation"]["messages"][1]["content"] = "different body"  # type: ignore[index]
    assert (
        gateway.client.post("/v1/inference", headers=gateway.headers, json=first).status_code == 200
    )

    collision = gateway.client.post("/v1/inference", headers=gateway.headers, json=changed)
    other_owner = gateway.client.post("/v1/inference", headers=gateway.other_headers, json=first)

    assert collision.status_code == 409
    assert collision.json()["error"] == {"code": "invalid_request", "retryable": False}
    assert other_owner.status_code == 403
    assert other_owner.json()["error"] == {"code": "forbidden", "retryable": False}
    assert gateway.worker.calls == 1


def test_acknowledgement_is_owner_only_idempotent_and_conflict_safe(gateway) -> None:  # type: ignore[no-untyped-def]
    document = gateway.request()
    gateway.client.post("/v1/inference", headers=gateway.headers, json=document)
    acknowledgement = {
        "protocol_version": 1,
        "request_id": REQUEST_ID,
        "disposition": "persisted",
    }

    forbidden = gateway.client.post(
        f"/v1/inference/{REQUEST_ID}/ack",
        headers=gateway.other_headers,
        json=acknowledgement,
    )
    first = gateway.client.post(
        f"/v1/inference/{REQUEST_ID}/ack", headers=gateway.headers, json=acknowledgement
    )
    repeat = gateway.client.post(
        f"/v1/inference/{REQUEST_ID}/ack", headers=gateway.headers, json=acknowledgement
    )
    conflict = gateway.client.post(
        f"/v1/inference/{REQUEST_ID}/ack",
        headers=gateway.headers,
        json={**acknowledgement, "disposition": "application_rejected"},
    )

    assert forbidden.status_code == 403
    assert first.status_code == repeat.status_code == 200
    assert first.json() == repeat.json()
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "acknowledgement_conflict"
    assert gateway.store.raw_persisted_values(REQUEST_ID) == (None, None)


def test_exact_concurrent_repeat_joins_one_worker_call(tmp_path) -> None:  # type: ignore[no-untyped-def]
    worker = FakeWorker()
    worker.release.clear()
    gateway = build_harness(tmp_path, worker=worker)
    document = gateway.request()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            gateway.client.post, "/v1/inference", headers=gateway.headers, json=document
        )
        assert worker.started.wait(2)
        repeat = pool.submit(
            gateway.client.post, "/v1/inference", headers=gateway.headers, json=document
        )
        worker.release.set()
        first_response = first.result(timeout=5)
        repeat_response = repeat.result(timeout=5)

    assert first_response.status_code == repeat_response.status_code == 200
    assert first_response.json() == repeat_response.json()
    assert worker.calls == 1


def test_stale_in_progress_read_reloads_completed_result_after_lane_clears(tmp_path) -> None:  # type: ignore[no-untyped-def]
    worker = FakeWorker()
    worker.release.clear()
    gateway = build_harness(tmp_path, worker=worker)
    document = gateway.request()
    request = InferenceRequest.model_validate(document)
    credential = gateway.credentials.authenticate(TOKEN)
    assert credential is not None
    digest = request.canonical_digest()
    service = gateway.client.app.state.gateway_service

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(service.infer, credential, request)
        assert worker.started.wait(2)
        stale = gateway.store.get_owned(
            request.request_id, credential.identity_hash, digest, gateway.clock()
        )
        assert stale.state == "in_progress"
        worker.release.set()
        completed = first.result(timeout=5)

    replay = service._join_active(stale, credential, digest)

    assert replay == completed
    assert worker.calls == 1


def test_durable_and_worker_admission_are_bounded(tmp_path) -> None:  # type: ignore[no-untyped-def]
    worker = FakeWorker()
    worker.release.clear()
    gateway = build_harness(
        tmp_path,
        worker=worker,
        max_open_total=1,
        max_open_per_credential=1,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            gateway.client.post,
            "/v1/inference",
            headers=gateway.headers,
            json=gateway.request(),
        )
        assert worker.started.wait(2)
        second_document = gateway.request(request_id="22345678-1234-4234-8234-123456789abc")
        second = gateway.client.post("/v1/inference", headers=gateway.headers, json=second_document)
        worker.release.set()
        assert first.result(timeout=5).status_code == 200

    assert second.status_code == 429
    assert second.json()["error"] == {
        "code": "capacity_limited",
        "retryable": True,
        "retry_after_seconds": 15,
    }
    assert gateway.worker.calls == 1


def test_expiry_boundaries_fail_before_dispatch(gateway) -> None:  # type: ignore[no-untyped-def]
    past = gateway.request(
        request_expires_at=(gateway.clock() - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    exact_now = gateway.request(
        request_id="22345678-1234-4234-8234-123456789abc",
        request_expires_at=gateway.clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    too_far = gateway.request(
        request_id="32345678-1234-4234-8234-123456789abc",
        request_expires_at=(gateway.clock() + timedelta(seconds=901)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    )

    assert (
        gateway.client.post("/v1/inference", headers=gateway.headers, json=past).status_code == 409
    )
    assert (
        gateway.client.post("/v1/inference", headers=gateway.headers, json=exact_now).status_code
        == 409
    )
    assert (
        gateway.client.post("/v1/inference", headers=gateway.headers, json=too_far).status_code
        == 422
    )
    assert gateway.worker.calls == 0


def test_expiry_is_rechecked_after_durable_transition(gateway, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mark_in_progress = gateway.store.mark_in_progress

    def delayed_mark(*args, **kwargs):  # type: ignore[no-untyped-def]
        record = mark_in_progress(*args, **kwargs)
        gateway.clock.advance(301)
        return record

    monkeypatch.setattr(gateway.store, "mark_in_progress", delayed_mark)

    response = gateway.client.post("/v1/inference", headers=gateway.headers, json=gateway.request())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "request_expired"
    assert gateway.worker.calls == 0


def test_unsupported_schema_evaluation_is_rejected_before_dispatch(gateway) -> None:  # type: ignore[no-untyped-def]
    hostile = gateway.request()
    hostile["generation"]["response_schema"] = {  # type: ignore[index]
        "type": "object",
        "properties": {"value": {"type": "string", "pattern": "(a+)+$"}},
    }
    dangling = deepcopy(hostile)
    dangling["generation"]["response_schema"] = {"$ref": "#/missing"}  # type: ignore[index]
    scalar = deepcopy(hostile)
    scalar["generation"]["response_schema"] = {"type": "string"}  # type: ignore[index]

    hostile_response = gateway.client.post("/v1/inference", headers=gateway.headers, json=hostile)
    dangling_response = gateway.client.post("/v1/inference", headers=gateway.headers, json=dangling)
    scalar_response = gateway.client.post("/v1/inference", headers=gateway.headers, json=scalar)

    assert (
        hostile_response.status_code
        == dangling_response.status_code
        == scalar_response.status_code
        == 422
    )
    assert gateway.worker.calls == 0


def test_known_worker_unavailability_can_retry_same_identity(gateway) -> None:  # type: ignore[no-untyped-def]
    gateway.worker.error = WorkerUnavailable("offline")
    document = gateway.request()
    unavailable = gateway.client.post("/v1/inference", headers=gateway.headers, json=document)

    gateway.worker.error = None
    completed = gateway.client.post("/v1/inference", headers=gateway.headers, json=document)

    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "worker_unavailable"
    assert completed.status_code == 200
    assert gateway.worker.calls == 2


def test_ambiguous_worker_outcome_is_not_redispatched_after_restart(tmp_path) -> None:  # type: ignore[no-untyped-def]
    worker = FakeWorker()
    worker.error = WorkerOutcomeAmbiguous("lost response")
    first_gateway = build_harness(tmp_path, worker=worker)
    document = first_gateway.request()
    first = first_gateway.client.post("/v1/inference", headers=first_gateway.headers, json=document)

    restarted_worker = FakeWorker()
    restarted = build_harness(
        tmp_path,
        database_name=first_gateway.settings.database_path.name,
        clock=first_gateway.clock,
        worker=restarted_worker,
    )
    repeat = restarted.client.post("/v1/inference", headers=restarted.headers, json=document)

    assert first.status_code == repeat.status_code == 504
    assert first.json()["error"]["code"] == repeat.json()["error"]["code"] == "inference_timeout"
    assert worker.calls == 1
    assert restarted_worker.calls == 0


def test_unexpected_worker_failure_is_ambiguous_and_not_redispatched(gateway) -> None:  # type: ignore[no-untyped-def]
    gateway.worker.error = RuntimeError("worker defect")
    document = gateway.request()

    first = gateway.client.post("/v1/inference", headers=gateway.headers, json=document)
    repeat = gateway.client.post("/v1/inference", headers=gateway.headers, json=document)

    assert first.status_code == repeat.status_code == 504
    assert first.json()["error"]["code"] == repeat.json()["error"]["code"] == "inference_timeout"
    assert gateway.worker.calls == 1


def test_late_worker_output_is_discarded(gateway) -> None:  # type: ignore[no-untyped-def]
    gateway.worker.before_return = lambda: gateway.clock.advance(301)
    response = gateway.client.post("/v1/inference", headers=gateway.headers, json=gateway.request())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "request_expired"
    assert b"private generated result" not in gateway.settings.database_path.read_bytes()


def test_request_body_size_boundary_is_enforced_before_validation(tmp_path) -> None:  # type: ignore[no-untyped-def]
    seed = build_harness(tmp_path / "seed")
    body = json.dumps(seed.request(), separators=(",", ":")).encode()
    at_limit = build_harness(tmp_path / "at", request_max_bytes=len(body))
    below_limit = build_harness(tmp_path / "below", request_max_bytes=len(body) - 1)

    accepted = at_limit.client.post(
        "/v1/inference",
        headers={**at_limit.headers, "Content-Type": "application/json"},
        content=body,
    )
    rejected = below_limit.client.post(
        "/v1/inference",
        headers={**below_limit.headers, "Content-Type": "application/json"},
        content=body,
    )

    assert accepted.status_code == 200
    assert rejected.status_code == 422
    assert below_limit.worker.calls == 0


def test_restart_replays_completed_ciphertext_without_worker_call(tmp_path) -> None:  # type: ignore[no-untyped-def]
    first = build_harness(tmp_path)
    document = first.request()
    original = first.client.post("/v1/inference", headers=first.headers, json=document)

    restarted_worker = FakeWorker()
    restarted_store = RequestStore(
        first.settings.database_path,
        first.store.cipher,
        max_open_total=16,
        max_open_per_credential=4,
        tombstone_retention_seconds=86_400,
    )
    restarted = TestClient(
        create_app(
            replace(first.settings, deployment_id="replacement-deployment"),
            credentials=first.credentials,
            store=restarted_store,
            worker=restarted_worker,  # type: ignore[arg-type]
            clock=first.clock,
        )
    )
    replay = restarted.post("/v1/inference", headers=first.headers, json=document)

    assert replay.status_code == 200
    assert replay.json() == original.json()
    assert replay.json()["provenance"] == {
        "task_policy_version": 1,
        "deployment_id": "test-deployment",
    }
    assert restarted_worker.calls == 0


def test_scheduled_maintenance_removes_idle_expired_ciphertext(tmp_path) -> None:  # type: ignore[no-untyped-def]
    gateway = build_harness(tmp_path, maintenance_interval_seconds=0.01)

    with gateway.client as client:
        completed = client.post("/v1/inference", headers=gateway.headers, json=gateway.request())
        assert completed.status_code == 200
        assert all(value is not None for value in gateway.store.raw_persisted_values(REQUEST_ID))

        gateway.clock.advance(301)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if gateway.store.raw_persisted_values(REQUEST_ID) == (None, None):
                break
            time.sleep(0.01)

    assert gateway.store.raw_persisted_values(REQUEST_ID) == (None, None)
