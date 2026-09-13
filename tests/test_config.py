from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from local_inference_gateway.config import (
    ConfigurationError,
    CredentialStore,
    LMStudioFallbackSettings,
    Settings,
    read_private_token,
)
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


def write_tls_files(tmp_path: Path) -> tuple[Path, Path]:
    certificate = tmp_path / "gateway.crt"
    key = tmp_path / "gateway.key"
    certificate.write_text("test certificate", encoding="ascii")
    certificate.chmod(0o644)
    key.write_text("test private key", encoding="ascii")
    key.chmod(0o600)
    return certificate, key


def write_token(tmp_path: Path, mode: int = 0o600) -> Path:
    token = tmp_path / "lm-studio.token"
    token.write_text("lm-studio-private-test-token-000000", encoding="ascii")
    token.chmod(mode)
    return token


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


@pytest.mark.parametrize("interval", [0.0, float("nan"), float("inf"), 3_600.01])
def test_settings_reject_invalid_maintenance_intervals(tmp_path: Path, interval: float) -> None:
    with pytest.raises(ConfigurationError, match="maintenance interval"):
        Settings(
            database_path=tmp_path / "db",
            credentials_path=tmp_path / "credentials",
            encryption_key_path=tmp_path / "key",
            ollama_base_url="http://127.0.0.1:11434",
            ollama_model="model",
            deployment_id="deployment",
            maintenance_interval_seconds=interval,
        )


@pytest.mark.parametrize("interval", [0.01, 3_600.0])
def test_settings_accept_maintenance_interval_boundaries(tmp_path: Path, interval: float) -> None:
    settings = Settings(
        database_path=tmp_path / "db",
        credentials_path=tmp_path / "credentials",
        encryption_key_path=tmp_path / "key",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="model",
        deployment_id="deployment",
        maintenance_interval_seconds=interval,
    )

    assert settings.maintenance_interval_seconds == interval


def test_settings_preserve_loopback_http_default(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "db",
        credentials_path=tmp_path / "credentials",
        encryption_key_path=tmp_path / "key",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="model",
        deployment_id="deployment",
    )

    assert settings.bind_host == "127.0.0.1"
    assert settings.tls_certificate_path is None
    assert settings.tls_key_path is None
    assert settings.lm_studio_fallback is None


def test_lm_studio_fallback_requires_private_token_and_bounded_loopback_config(
    tmp_path: Path,
) -> None:
    token = write_token(tmp_path)

    fallback = LMStudioFallbackSettings(
        base_url="http://127.0.0.1:1234/",
        model="pinned-fallback-model",
        token_path=token,
        idle_ttl_seconds=300,
    )

    assert fallback.base_url == "http://127.0.0.1:1234"
    assert read_private_token(token, "LM Studio") == "lm-studio-private-test-token-000000"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"base_url": "http://192.168.1.20:1234"}, "loopback"),
        ({"model": ""}, "model identifier"),
        ({"model": " padded-model"}, "model identifier"),
        ({"idle_ttl_seconds": 0}, "idle TTL"),
        ({"idle_ttl_seconds": 3_601}, "idle TTL"),
        ({"idle_ttl_seconds": True}, "idle TTL"),
    ],
)
def test_lm_studio_fallback_rejects_unsafe_or_unbounded_settings(
    tmp_path: Path, mutation: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "base_url": "http://127.0.0.1:1234",
        "model": "pinned-fallback-model",
        "token_path": write_token(tmp_path),
        "idle_ttl_seconds": 300,
    }
    values.update(mutation)

    with pytest.raises(ConfigurationError, match=message):
        LMStudioFallbackSettings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("idle_ttl_seconds", [1, 3_600])
def test_lm_studio_fallback_accepts_idle_ttl_boundaries(
    tmp_path: Path, idle_ttl_seconds: int
) -> None:
    fallback = LMStudioFallbackSettings(
        base_url="http://127.0.0.1:1234",
        model="pinned-fallback-model",
        token_path=write_token(tmp_path),
        idle_ttl_seconds=idle_ttl_seconds,
    )

    assert fallback.idle_ttl_seconds == idle_ttl_seconds


def test_lm_studio_fallback_rejects_nonprivate_token_file(tmp_path: Path) -> None:
    token = write_token(tmp_path, mode=0o644)

    with pytest.raises(ConfigurationError, match="owner-private"):
        LMStudioFallbackSettings(
            base_url="http://127.0.0.1:1234",
            model="pinned-fallback-model",
            token_path=token,
        )


def test_settings_from_env_reads_complete_lm_studio_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = write_token(tmp_path)
    environment = {
        "GATEWAY_DATABASE_FILE": str(tmp_path / "db"),
        "GATEWAY_CREDENTIALS_FILE": str(tmp_path / "credentials"),
        "GATEWAY_ENCRYPTION_KEY_FILE": str(tmp_path / "result-key"),
        "GATEWAY_OLLAMA_URL": "http://127.0.0.1:11434",
        "GATEWAY_OLLAMA_MODEL": "primary-model",
        "GATEWAY_DEPLOYMENT_ID": "deployment",
        "GATEWAY_LM_STUDIO_URL": "http://127.0.0.1:1234",
        "GATEWAY_LM_STUDIO_MODEL": "fallback-model",
        "GATEWAY_LM_STUDIO_TOKEN_FILE": str(token),
        "GATEWAY_LM_STUDIO_IDLE_TTL_SECONDS": "120",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    settings = Settings.from_env()

    assert settings.lm_studio_fallback == LMStudioFallbackSettings(
        "http://127.0.0.1:1234", "fallback-model", token, 120
    )


@pytest.mark.parametrize(
    "configured",
    [
        {"GATEWAY_LM_STUDIO_URL": "http://127.0.0.1:1234"},
        {"GATEWAY_LM_STUDIO_MODEL": "fallback-model"},
        {"GATEWAY_LM_STUDIO_TOKEN_FILE": "/private/missing-token"},
        {"GATEWAY_LM_STUDIO_IDLE_TTL_SECONDS": "120"},
    ],
)
def test_settings_from_env_rejects_partial_lm_studio_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: dict[str, str]
) -> None:
    environment = {
        "GATEWAY_DATABASE_FILE": str(tmp_path / "db"),
        "GATEWAY_CREDENTIALS_FILE": str(tmp_path / "credentials"),
        "GATEWAY_ENCRYPTION_KEY_FILE": str(tmp_path / "result-key"),
        "GATEWAY_OLLAMA_URL": "http://127.0.0.1:11434",
        "GATEWAY_OLLAMA_MODEL": "primary-model",
        "GATEWAY_DEPLOYMENT_ID": "deployment",
    }
    for name in (
        "GATEWAY_LM_STUDIO_URL",
        "GATEWAY_LM_STUDIO_MODEL",
        "GATEWAY_LM_STUDIO_TOKEN_FILE",
        "GATEWAY_LM_STUDIO_IDLE_TTL_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in (environment | configured).items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ConfigurationError, match="configuration must be complete"):
        Settings.from_env()


@pytest.mark.parametrize("bind_host", ["192.168.1.50", "fd12:3456:789a::50"])
def test_settings_accept_private_listener_with_tls(tmp_path: Path, bind_host: str) -> None:
    certificate, key = write_tls_files(tmp_path)

    settings = Settings(
        database_path=tmp_path / "db",
        credentials_path=tmp_path / "credentials",
        encryption_key_path=tmp_path / "key",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="model",
        deployment_id="deployment",
        bind_host=bind_host,
        tls_certificate_path=certificate,
        tls_key_path=key,
    )

    assert settings.bind_host == bind_host
    assert settings.tls_certificate_path == certificate
    assert settings.tls_key_path == key


def test_settings_from_env_reads_private_listener_tls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    certificate, key = write_tls_files(tmp_path)
    environment = {
        "GATEWAY_DATABASE_FILE": str(tmp_path / "db"),
        "GATEWAY_CREDENTIALS_FILE": str(tmp_path / "credentials"),
        "GATEWAY_ENCRYPTION_KEY_FILE": str(tmp_path / "result-key"),
        "GATEWAY_OLLAMA_URL": "http://127.0.0.1:11434",
        "GATEWAY_OLLAMA_MODEL": "model",
        "GATEWAY_DEPLOYMENT_ID": "deployment",
        "GATEWAY_BIND_HOST": "192.168.1.50",
        "GATEWAY_BIND_PORT": "9443",
        "GATEWAY_TLS_CERTIFICATE_FILE": str(certificate),
        "GATEWAY_TLS_KEY_FILE": str(key),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    settings = Settings.from_env()

    assert settings.bind_host == "192.168.1.50"
    assert settings.bind_port == 9443
    assert settings.tls_certificate_path == certificate
    assert settings.tls_key_path == key


def test_settings_reject_non_loopback_plaintext_and_partial_tls(tmp_path: Path) -> None:
    certificate, _ = write_tls_files(tmp_path)
    common = {
        "database_path": tmp_path / "db",
        "credentials_path": tmp_path / "credentials",
        "encryption_key_path": tmp_path / "key",
        "ollama_base_url": "http://127.0.0.1:11434",
        "ollama_model": "model",
        "deployment_id": "deployment",
    }

    with pytest.raises(ConfigurationError, match="requires TLS"):
        Settings(**common, bind_host="192.168.1.50")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError, match="configured together"):
        Settings(**common, tls_certificate_path=certificate)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bind_host",
    [
        "0.0.0.0",
        "::",
        "8.8.8.8",
        "224.0.0.1",
        "169.254.10.20",
        "192.0.2.1",
        "2001:db8::1",
        "gateway.local",
    ],
)
def test_settings_reject_unsafe_network_bind_classes(tmp_path: Path, bind_host: str) -> None:
    certificate, key = write_tls_files(tmp_path)

    with pytest.raises(ConfigurationError, match=r"IP address|concrete private"):
        Settings(
            database_path=tmp_path / "db",
            credentials_path=tmp_path / "credentials",
            encryption_key_path=tmp_path / "key",
            ollama_base_url="http://127.0.0.1:11434",
            ollama_model="model",
            deployment_id="deployment",
            bind_host=bind_host,
            tls_certificate_path=certificate,
            tls_key_path=key,
        )


def test_settings_reject_unsafe_tls_file_permissions(tmp_path: Path) -> None:
    certificate, key = write_tls_files(tmp_path)
    common = {
        "database_path": tmp_path / "db",
        "credentials_path": tmp_path / "credentials",
        "encryption_key_path": tmp_path / "key",
        "ollama_base_url": "http://127.0.0.1:11434",
        "ollama_model": "model",
        "deployment_id": "deployment",
        "bind_host": "192.168.1.50",
        "tls_certificate_path": certificate,
        "tls_key_path": key,
    }

    key.chmod(0o640)
    with pytest.raises(ConfigurationError, match="owner-private"):
        Settings(**common)  # type: ignore[arg-type]

    key.chmod(0o600)
    certificate.chmod(0o666)
    with pytest.raises(ConfigurationError, match="group/world-writable"):
        Settings(**common)  # type: ignore[arg-type]


@pytest.mark.skipif(os.name != "posix", reason="O_NOFOLLOW is a POSIX boundary")
@pytest.mark.parametrize("symlinked", ["certificate", "key"])
def test_settings_reject_symlinked_tls_files(tmp_path: Path, symlinked: str) -> None:
    certificate, key = write_tls_files(tmp_path)
    target = certificate if symlinked == "certificate" else key
    replacement = tmp_path / f"linked-{target.name}"
    replacement.symlink_to(target)

    with pytest.raises(ConfigurationError, match="unavailable"):
        Settings(
            database_path=tmp_path / "db",
            credentials_path=tmp_path / "credentials",
            encryption_key_path=tmp_path / "key",
            ollama_base_url="http://127.0.0.1:11434",
            ollama_model="model",
            deployment_id="deployment",
            bind_host="192.168.1.50",
            tls_certificate_path=(replacement if symlinked == "certificate" else certificate),
            tls_key_path=replacement if symlinked == "key" else key,
        )
