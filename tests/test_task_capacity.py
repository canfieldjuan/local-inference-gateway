from __future__ import annotations

import json
from typing import Any

import httpx

from local_inference_gateway.worker import OllamaWorker
from tests.test_passage_schema import passage_client


def capacity_worker(parameters: object, residents: object = None) -> tuple[OllamaWorker, list[str]]:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/models":
            body = {"data": [{"id": "candidate"}]}
        elif request.url.path == "/api/show":
            body = {"parameters": parameters}
        elif request.url.path == "/api/ps":
            body = {"models": [] if residents is None else residents}
        else:
            raise AssertionError("capacity failure must not dispatch inference")
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=httpx.ByteStream(json.dumps(body).encode()),
        )

    worker = OllamaWorker("http://127.0.0.1:11434", "candidate")
    worker._client = lambda timeout: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=httpx.MockTransport(handler), timeout=timeout
    )
    return worker, calls


def test_authorized_document_profile_exposes_server_capacity(gateway: Any) -> None:
    with passage_client(gateway) as client:
        response = client.get(
            "/v1/tasks/document.summary.step/2/profile", headers=gateway.other_headers
        )
        assert response.status_code == 200
        assert response.json()["profile"]["context_tokens"] == 32768
        assert response.json()["profile"]["max_output_tokens"] == 4096


def test_small_worker_is_not_available_for_document_v2() -> None:
    worker, _ = capacity_worker("num_ctx 8192\n")
    assert not worker.health(("document.summary.step", 2))


def test_profile_authorization_and_legacy_health_shape(gateway: Any) -> None:
    path = "/v1/tasks/document.summary.step/2/profile"
    assert gateway.client.get(path).status_code == 401
    assert gateway.client.get(path, headers=gateway.other_headers).status_code == 403
    with passage_client(gateway) as client:
        payload = client.get(path, headers=gateway.other_headers).json()
        assert payload == {
            "protocol_version": 1,
            "task": {"id": "document.summary.step", "version": 2},
            "profile": {
                "version": 1,
                "context_tokens": 32768,
                "max_output_tokens": 4096,
                "max_schema_bytes": 250000,
                "schema_features": ["bounded_source_passages_v1"],
            },
            "status": "available",
            "diagnostic_code": "ready",
        }
        gateway.worker.available = False
        payload = client.get(path, headers=gateway.other_headers).json()
        assert payload["status"] == "unavailable"
        assert gateway.worker.calls == 0
    assert (
        gateway.client.get(
            "/v1/tasks/document.summary.step/1/profile", headers=gateway.other_headers
        ).status_code
        == 404
    )
    health = gateway.client.get("/v1/health", headers=gateway.other_headers).json()
    assert set(health) == {"protocol_version", "tasks"}
    assert set(health["tasks"][0]) == {"id", "version", "status", "diagnostic_code"}


def test_configured_capacity_boundaries_and_defaults() -> None:
    from local_inference_gateway.worker import WorkerAvailability

    for count in [32768, 32769, 1048575, 1048576]:
        worker, calls = capacity_worker(f"temperature 0\nnum_ctx {count}\n")
        assert worker.availability(("document.summary.step", 2)) is WorkerAvailability.AVAILABLE
        assert calls == ["/v1/models", "/api/show", "/api/ps"]
    for count in [1, 8192, 32767]:
        worker, calls = capacity_worker(f"num_ctx {count}\n")
        assert worker.availability(("document.summary.step", 2)) is WorkerAvailability.UNAVAILABLE
        assert "/api/ps" not in calls
    for value in [
        None,
        False,
        0,
        "",
        [],
        {},
        "num_ctx 0",
        "num_ctx False",
        "num_ctx -1",
        "num_ctx 32768.0",
        "num_ctx +32768",
        "num_ctx 1048577",
        "num_ctx 99999999999999999999",
        "num_ctx",
        "num_ctx 32768 extra",
        "num_ctx 32768\nnum_ctx 8192",
        "num_ctx 32768\nnum_ctx 32768",
        "temperature 0",
        "num_ctx \uff13\uff12\uff17\uff16\uff18",
    ]:
        worker, _ = capacity_worker(value)
        assert worker.availability(("document.summary.step", 2)) is WorkerAvailability.UNKNOWN
    worker, calls = capacity_worker(None)
    assert worker.health(("document.summary.step", 1))
    assert worker.health(("email.analyze", 1))
    assert calls == ["/v1/models", "/v1/models"]


def test_resident_capacity_must_not_contradict_configuration() -> None:
    for value in [32768, 32769, 1048576]:
        worker, _ = capacity_worker(
            "num_ctx 32768", [{"model": "candidate", "context_length": value}]
        )
        assert worker.health(("document.summary.step", 2))
    for value in [None, False, 0, -1, "32768", 32768.0, 32767, 1048577]:
        worker, _ = capacity_worker(
            "num_ctx 32768", [{"name": "candidate", "context_length": value}]
        )
        assert not worker.health(("document.summary.step", 2))
    for residents in [
        False,
        {},
        "",
        [None],
        [{}],
        [{"name": 0}],
        [{"name": "candidate", "model": "different", "context_length": 32768}],
        [{"name": "candidate", "context_length": 32768}] * 2,
        [{"name": "candidate", "context_length": 32768}, {}],
    ]:
        worker, _ = capacity_worker("num_ctx 32768", residents)
        assert not worker.health(("document.summary.step", 2))
    worker, _ = capacity_worker("num_ctx 32768", [{"name": "other", "context_length": 8192}])
    assert worker.health(("document.summary.step", 2))


def test_generation_checks_capacity_before_submission(gateway: Any) -> None:
    import pytest

    from local_inference_gateway.worker import WorkerUnavailable
    from tests.test_passage_schema import admitted_request, passage_schema

    request = admitted_request(gateway, passage_schema(1))
    for configured, resident in [
        ("num_ctx 8192", []),
        (None, []),
        ("num_ctx 32768", [{"name": "candidate", "context_length": 8192}]),
    ]:
        worker, calls = capacity_worker(configured, resident)
        with pytest.raises(WorkerUnavailable, match="capacity"):
            worker.infer(request, 5)
        assert "/v1/chat/completions" not in calls


def test_capacity_timeout_is_known_not_submitted(gateway: Any) -> None:
    import asyncio

    import pytest

    from local_inference_gateway.worker import WorkerUnavailable
    from tests.test_passage_schema import admitted_request, passage_schema

    worker, _ = capacity_worker("num_ctx 32768")

    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.1)
        raise AssertionError("deadline must cancel metadata read")

    worker._client = lambda timeout: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=httpx.MockTransport(slow), timeout=timeout
    )
    with pytest.raises(WorkerUnavailable, match="before dispatch"):
        worker.infer(admitted_request(gateway, passage_schema(1)), 0.01)


def test_lm_studio_does_not_claim_unverified_extended_capacity(gateway: Any) -> None:
    import pytest

    from local_inference_gateway.worker import LMStudioWorker, WorkerUnavailable
    from tests.test_passage_schema import admitted_request, passage_schema

    worker = LMStudioWorker("http://127.0.0.1:1234", "candidate", "synthetic-token", 300)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.path == "/v1/models"
        return httpx.Response(200, stream=httpx.ByteStream(b'{"data":[{"id":"candidate"}]}'))

    worker._client = lambda timeout: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=httpx.MockTransport(handler), timeout=timeout
    )
    assert worker.health(("document.summary.step", 1))
    assert not worker.health(("document.summary.step", 2))
    with pytest.raises(WorkerUnavailable):
        worker.infer(admitted_request(gateway, passage_schema(1)), 5)
    assert calls == ["/v1/models", "/v1/models"]


def test_malformed_capacity_json_is_known_not_submitted(gateway: Any) -> None:
    import pytest

    from local_inference_gateway.worker import WorkerUnavailable
    from tests.test_passage_schema import admitted_request, passage_schema

    worker, _ = capacity_worker("num_ctx 32768")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.path == "/api/show"
        return httpx.Response(200, stream=httpx.ByteStream(b'{"parameters":'))

    worker._client = lambda timeout: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=httpx.MockTransport(handler), timeout=timeout
    )
    with pytest.raises(WorkerUnavailable, match="before dispatch"):
        worker.infer(admitted_request(gateway, passage_schema(1)), 5)
    assert calls == ["/api/show"]
