from __future__ import annotations

import ipaddress
import socket
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from local_inference_gateway import __main__
from local_inference_gateway.config import Settings


def test_entrypoint_passes_explicit_single_process_tls_settings(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    certificate = tmp_path / "gateway.crt"
    key = tmp_path / "gateway.key"
    certificate.write_text("test certificate", encoding="ascii")
    certificate.chmod(0o644)
    key.write_text("test key", encoding="ascii")
    key.chmod(0o600)
    settings = Settings(
        database_path=tmp_path / "db",
        credentials_path=tmp_path / "credentials",
        encryption_key_path=tmp_path / "result-key",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="model",
        deployment_id="deployment",
        bind_host="192.168.1.50",
        bind_port=9443,
        tls_certificate_path=certificate,
        tls_key_path=key,
    )
    application = object()
    calls: list[tuple[object, dict[str, object]]] = []

    monkeypatch.setattr(__main__.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(__main__, "create_app", lambda supplied: application)
    monkeypatch.setattr(
        __main__.uvicorn,
        "run",
        lambda app, **kwargs: calls.append((app, kwargs)),
    )

    __main__.main()

    assert calls == [
        (
            application,
            {
                "host": "192.168.1.50",
                "port": 9443,
                "access_log": True,
                "proxy_headers": False,
                "workers": 1,
                "ssl_certfile": str(certificate),
                "ssl_keyfile": str(key),
            },
        )
    ]


def test_real_uvicorn_tls_listener_serves_private_liveness(gateway, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    certificate_path, key_path = _write_ephemeral_certificate(tmp_path)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    config = uvicorn.Config(
        gateway.client.app,
        log_level="error",
        access_log=False,
        proxy_headers=False,
        workers=1,
        ssl_certfile=str(certificate_path),
        ssl_keyfile=str(key_path),
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )

    try:
        thread.start()
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started

        response = httpx.get(f"https://127.0.0.1:{port}/health/live", verify=False, timeout=5)

        assert response.status_code == 200
        assert response.json() == {"protocol_version": 1, "status": "live"}
    finally:
        server.should_exit = True
        thread.join(5)
        listener.close()
    assert not thread.is_alive()


def _write_ephemeral_certificate(tmp_path: Path) -> tuple[Path, Path]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "gateway-test")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(minutes=5))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )
    certificate_path = tmp_path / "gateway.crt"
    key_path = tmp_path / "gateway.key"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    certificate_path.chmod(0o644)
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    return certificate_path, key_path
