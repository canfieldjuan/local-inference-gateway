from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from local_inference_gateway.config import ConfigurationError, CredentialStore, Settings
from tests.conftest import TOKEN


def write_credentials(path: Path, document: dict[str, object], mode: int = 0o600) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(mode)


def valid_credentials() -> dict[str, object]:
    return {
        "version": 1,
        "credentials": [
            {
                "id": "email-watcher",
                "token_sha256": hashlib.sha256(TOKEN.encode("ascii")).hexdigest(),
                "tasks": [{"id": "email.analyze", "version": 1}],
            }
        ],
    }


def test_credentials_load_hashes_and_authenticate_without_plaintext(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    write_credentials(path, valid_credentials())

    store = CredentialStore.from_file(path)

    assert store.authenticate(TOKEN) is not None
    assert store.authenticate("wrong-token-that-is-long-enough-000000") is None
    assert TOKEN not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document.update(version=True),
        lambda document: document["credentials"][0].update(token_sha256="not-a-digest"),
        lambda document: document["credentials"][0].update(tasks=[]),
        lambda document: document["credentials"].append(document["credentials"][0].copy()),
    ],
)
def test_credentials_reject_partial_or_duplicate_configuration(
    tmp_path: Path,
    mutation,  # type: ignore[no-untyped-def]
) -> None:
    document = valid_credentials()
    mutation(document)
    path = tmp_path / "credentials.json"
    write_credentials(path, document)

    with pytest.raises(ConfigurationError):
        CredentialStore.from_file(path)


def test_credentials_require_owner_private_file(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    write_credentials(path, valid_credentials(), mode=0o644)

    with pytest.raises(ConfigurationError, match="owner-private"):
        CredentialStore.from_file(path)


@pytest.mark.skipif(os.name != "posix", reason="O_NOFOLLOW is a POSIX boundary")
def test_credentials_reject_symlinked_file(tmp_path: Path) -> None:
    target = tmp_path / "real-credentials.json"
    path = tmp_path / "credentials.json"
    write_credentials(target, valid_credentials())
    path.symlink_to(target)

    with pytest.raises(ConfigurationError, match="unavailable"):
        CredentialStore.from_file(path)


@pytest.mark.parametrize(
    "url",
    [
        "http://0.0.0.0:11434",
        "http://192.168.1.10:11434",
        "https://127.0.0.1:11434",
        "http://user@127.0.0.1:11434",
        "http://127.0.0.1:11434/v1",
    ],
)
def test_settings_reject_non_private_worker_authorities(tmp_path: Path, url: str) -> None:
    with pytest.raises(ConfigurationError, match="loopback"):
        Settings(
            database_path=tmp_path / "db",
            credentials_path=tmp_path / "credentials",
            encryption_key_path=tmp_path / "key",
            ollama_base_url=url,
            ollama_model="model",
            deployment_id="deployment",
        )
