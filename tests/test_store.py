from __future__ import annotations

import base64
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from local_inference_gateway.contracts import InferenceRequest
from local_inference_gateway.store import (
    AcknowledgementConflict,
    RequestExpired,
    RequestNotTerminal,
    RequestStore,
    ResultCipher,
    StoreError,
)


def test_result_cipher_requires_owner_private_32_byte_key(tmp_path: Path) -> None:
    path = tmp_path / "key"
    path.write_bytes(base64.urlsafe_b64encode(b"short"))
    path.chmod(0o600)
    with pytest.raises(StoreError, match="32 bytes"):
        ResultCipher.from_file(path)

    path.write_bytes(base64.urlsafe_b64encode(b"k" * 32))
    path.chmod(0o644)
    with pytest.raises(StoreError, match="owner-private"):
        ResultCipher.from_file(path)


def test_store_encrypts_then_acknowledges_result(gateway) -> None:  # type: ignore[no-untyped-def]
    request = InferenceRequest.model_validate(gateway.request())
    credential = gateway.credentials.authenticate(gateway.headers["Authorization"].split()[1])
    assert credential is not None
    digest = request.canonical_digest()
    record = gateway.store.admit(request, credential.identity_hash, digest, gateway.clock())
    gateway.store.mark_in_progress(record.request_id, "attempt-1", gateway.clock())

    assert gateway.store.complete(
        record.request_id,
        "attempt-1",
        "application/json",
        '{"private":"generated"}',
        deployment_id="test-deployment",
        task_policy_version=1,
        now=gateway.clock(),
    )
    completed = gateway.store.get_owned(
        record.request_id, credential.identity_hash, digest, gateway.clock()
    )
    nonce, ciphertext = gateway.store.raw_persisted_values(record.request_id)

    assert completed.state == "completed"
    assert gateway.store.output(completed) == '{"private":"generated"}'
    assert nonce is not None and ciphertext is not None
    assert b"generated" not in gateway.settings.database_path.read_bytes()

    acknowledged = gateway.store.acknowledge(
        record.request_id, credential.identity_hash, "persisted", gateway.clock()
    )
    repeat = gateway.store.acknowledge(
        record.request_id, credential.identity_hash, "persisted", gateway.clock()
    )

    assert acknowledged.state == repeat.state == "acknowledged"
    assert gateway.store.raw_persisted_values(record.request_id) == (None, None)
    with pytest.raises(AcknowledgementConflict):
        gateway.store.acknowledge(
            record.request_id,
            credential.identity_hash,
            "application_rejected",
            gateway.clock(),
        )


def test_store_restart_marks_active_attempt_ambiguous(gateway) -> None:  # type: ignore[no-untyped-def]
    request = InferenceRequest.model_validate(gateway.request())
    credential = gateway.credentials.authenticate(gateway.headers["Authorization"].split()[1])
    assert credential is not None
    digest = request.canonical_digest()
    gateway.store.admit(request, credential.identity_hash, digest, gateway.clock())
    gateway.store.mark_in_progress(request.request_id, "attempt-1", gateway.clock())

    restarted = RequestStore(
        gateway.settings.database_path,
        gateway.store.cipher,
        max_open_total=16,
        max_open_per_credential=4,
        tombstone_retention_seconds=86_400,
    )
    restarted.initialize(gateway.clock())
    record = restarted.get_owned(
        request.request_id, credential.identity_hash, digest, gateway.clock()
    )

    assert record.state == "ambiguous"


def test_expiry_wins_before_late_completion(gateway) -> None:  # type: ignore[no-untyped-def]
    request = InferenceRequest.model_validate(gateway.request())
    credential = gateway.credentials.authenticate(gateway.headers["Authorization"].split()[1])
    assert credential is not None
    digest = request.canonical_digest()
    gateway.store.admit(request, credential.identity_hash, digest, gateway.clock())
    gateway.store.mark_in_progress(request.request_id, "attempt-1", gateway.clock())
    gateway.clock.advance(301)

    assert not gateway.store.complete(
        request.request_id,
        "attempt-1",
        "application/json",
        "late private output",
        deployment_id="test-deployment",
        task_policy_version=1,
        now=gateway.clock(),
    )
    with pytest.raises(RequestExpired):
        gateway.store.acknowledge(
            request.request_id, credential.identity_hash, "persisted", gateway.clock()
        )
    assert b"late private output" not in gateway.settings.database_path.read_bytes()


def test_acknowledgement_before_completion_is_rejected(gateway) -> None:  # type: ignore[no-untyped-def]
    document = gateway.request(
        request_expires_at=(gateway.clock() + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    request = InferenceRequest.model_validate(document)
    credential = gateway.credentials.authenticate(gateway.headers["Authorization"].split()[1])
    assert credential is not None
    gateway.store.admit(
        request, credential.identity_hash, request.canonical_digest(), gateway.clock()
    )

    with pytest.raises(RequestNotTerminal):
        gateway.store.acknowledge(
            request.request_id, credential.identity_hash, "persisted", gateway.clock()
        )


def test_initialize_rejects_partial_schema_without_creating_tables(tmp_path: Path, gateway) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "partial.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE schema_metadata (singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
    )
    connection.execute("INSERT INTO schema_metadata VALUES (1, 1)")
    connection.commit()
    connection.close()
    key_path = tmp_path / "key"
    key_path.write_bytes(base64.urlsafe_b64encode(b"k" * 32))
    key_path.chmod(0o600)
    store = RequestStore(
        path,
        ResultCipher.from_file(key_path),
        max_open_total=2,
        max_open_per_credential=1,
        tombstone_retention_seconds=60,
    )

    with pytest.raises(StoreError):
        store.initialize(gateway.clock())

    connection = sqlite3.connect(path)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    connection.close()
    assert tables == {"schema_metadata"}


def test_initialize_rejects_future_schema_without_mutating_rows(gateway) -> None:  # type: ignore[no-untyped-def]
    request = InferenceRequest.model_validate(gateway.request())
    credential = gateway.credentials.authenticate(gateway.headers["Authorization"].split()[1])
    assert credential is not None
    gateway.store.admit(
        request, credential.identity_hash, request.canonical_digest(), gateway.clock()
    )
    gateway.store.mark_in_progress(request.request_id, "active-attempt", gateway.clock())
    connection = sqlite3.connect(gateway.settings.database_path)
    connection.execute("UPDATE schema_metadata SET version = 2 WHERE singleton = 1")
    connection.commit()
    connection.close()
    restarted = RequestStore(
        gateway.settings.database_path,
        gateway.store.cipher,
        max_open_total=16,
        max_open_per_credential=4,
        tombstone_retention_seconds=86_400,
    )

    with pytest.raises(StoreError, match="version 2"):
        restarted.initialize(gateway.clock())

    connection = sqlite3.connect(gateway.settings.database_path)
    state = connection.execute(
        "SELECT state FROM inference_requests WHERE request_id = ?", (request.request_id,)
    ).fetchone()[0]
    connection.close()
    assert state == "in_progress"


def test_initialize_rejects_same_version_with_missing_provenance_without_mutation(gateway) -> None:  # type: ignore[no-untyped-def]
    request = InferenceRequest.model_validate(gateway.request())
    credential = gateway.credentials.authenticate(gateway.headers["Authorization"].split()[1])
    assert credential is not None
    gateway.store.admit(
        request, credential.identity_hash, request.canonical_digest(), gateway.clock()
    )
    gateway.store.mark_in_progress(request.request_id, "active-attempt", gateway.clock())
    connection = sqlite3.connect(gateway.settings.database_path)
    connection.execute("ALTER TABLE inference_requests DROP COLUMN producing_deployment_id")
    connection.commit()
    connection.close()
    restarted = RequestStore(
        gateway.settings.database_path,
        gateway.store.cipher,
        max_open_total=16,
        max_open_per_credential=4,
        tombstone_retention_seconds=86_400,
    )

    with pytest.raises(StoreError, match="schema columns"):
        restarted.initialize(gateway.clock())

    connection = sqlite3.connect(gateway.settings.database_path)
    state = connection.execute(
        "SELECT state FROM inference_requests WHERE request_id = ?", (request.request_id,)
    ).fetchone()[0]
    connection.close()
    assert state == "in_progress"
