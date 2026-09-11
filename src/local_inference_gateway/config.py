from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .contracts import MAX_REQUEST_BYTES

MAX_CONFIG_BYTES = 128 * 1024
MAX_TLS_FILE_BYTES = 1024 * 1024
MAX_CREDENTIALS = 100
TOKEN_DIGEST_LENGTH = 64
PRIVATE_BIND_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


class ConfigurationError(RuntimeError):
    """Gateway configuration is missing or unsafe."""


@dataclass(frozen=True)
class Credential:
    identity_hash: str
    token_digest: str
    tasks: frozenset[tuple[str, int]]


class CredentialStore:
    def __init__(self, credentials: tuple[Credential, ...]):
        self._credentials = credentials

    @classmethod
    def from_file(cls, path: Path) -> CredentialStore:
        document = _read_private_json(path, "credential")
        if (
            set(document) != {"version", "credentials"}
            or type(document.get("version")) is not int
            or document.get("version") != 1
        ):
            raise ConfigurationError("credential file must use schema version 1")
        raw_credentials = document.get("credentials")
        if (
            not isinstance(raw_credentials, list)
            or not 1 <= len(raw_credentials) <= MAX_CREDENTIALS
        ):
            raise ConfigurationError("credential file must contain a bounded non-empty list")
        credentials: list[Credential] = []
        identities: set[str] = set()
        token_digests: set[str] = set()
        for raw in raw_credentials:
            if not isinstance(raw, dict) or set(raw) != {"id", "token_sha256", "tasks"}:
                raise ConfigurationError("credential entries contain invalid fields")
            identity = raw.get("id")
            token_digest = raw.get("token_sha256")
            tasks = raw.get("tasks")
            if not isinstance(identity, str) or not 1 <= len(identity) <= 128:
                raise ConfigurationError("credential id is invalid")
            if (
                not isinstance(token_digest, str)
                or len(token_digest) != TOKEN_DIGEST_LENGTH
                or any(character not in "0123456789abcdef" for character in token_digest)
            ):
                raise ConfigurationError("credential token digest is invalid")
            parsed_tasks = _parse_tasks(tasks)
            identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            if identity_hash in identities or token_digest in token_digests:
                raise ConfigurationError("credential identities and token digests must be unique")
            identities.add(identity_hash)
            token_digests.add(token_digest)
            credentials.append(Credential(identity_hash, token_digest, parsed_tasks))
        return cls(tuple(credentials))

    def authenticate(self, token: str) -> Credential | None:
        if (
            not 32 <= len(token) <= 512
            or not token.isascii()
            or any(char.isspace() for char in token)
        ):
            return None
        candidate = hashlib.sha256(token.encode("ascii")).hexdigest()
        matched: Credential | None = None
        for credential in self._credentials:
            if hmac.compare_digest(candidate, credential.token_digest):
                matched = credential
        return matched


def _parse_tasks(value: object) -> frozenset[tuple[str, int]]:
    if not isinstance(value, list) or not value:
        raise ConfigurationError("credential tasks must be a non-empty list")
    parsed: set[tuple[str, int]] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"id", "version"}:
            raise ConfigurationError("credential task entries are invalid")
        task_id = item.get("id")
        version = item.get("version")
        if not isinstance(task_id, str) or not task_id or type(version) is not int or version < 1:
            raise ConfigurationError("credential task identity is invalid")
        parsed.add((task_id, version))
    if len(parsed) != len(value):
        raise ConfigurationError("credential task entries must be unique")
    return frozenset(parsed)


def _read_private_json(path: Path, label: str) -> dict[str, object]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_CONFIG_BYTES:
                raise ConfigurationError(f"{label} file is invalid")
            if os.name == "posix" and (stat.S_IMODE(metadata.st_mode) & 0o077):
                raise ConfigurationError(f"{label} file must be owner-private")
            encoded = stream.read(MAX_CONFIG_BYTES + 1)
        if not encoded or len(encoded) > MAX_CONFIG_BYTES:
            raise ConfigurationError(f"{label} file is invalid")
        document = json.loads(encoded)
    except ConfigurationError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise ConfigurationError(f"{label} file is unavailable or invalid") from exc
    if not isinstance(document, dict):
        raise ConfigurationError(f"{label} file must contain a JSON object")
    return document


def _loopback_worker_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except (ValueError, UnicodeError) as exc:
        raise ConfigurationError("Ollama URL is invalid") from exc
    if (
        parsed.scheme != "http"
        or not address.is_loopback
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError("Ollama must use an explicit loopback HTTP authority")
    return f"http://{f'[{address}]' if address.version == 6 else address}:{port}"


def _validate_tls_file(path: Path, label: str, *, owner_private: bool) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            mode = stat.S_IMODE(metadata.st_mode)
            if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_TLS_FILE_BYTES:
                raise ConfigurationError(f"{label} file is invalid")
            if os.name == "posix" and (
                (owner_private and mode & 0o077) or (not owner_private and mode & 0o022)
            ):
                qualifier = "owner-private" if owner_private else "not group/world-writable"
                raise ConfigurationError(f"{label} file must be {qualifier}")
    except ConfigurationError:
        raise
    except OSError as exc:
        raise ConfigurationError(f"{label} file is unavailable or invalid") from exc


@dataclass(frozen=True)
class Settings:
    database_path: Path
    credentials_path: Path
    encryption_key_path: Path
    ollama_base_url: str
    ollama_model: str
    deployment_id: str
    bind_host: str = "127.0.0.1"
    bind_port: int = 8787
    tls_certificate_path: Path | None = None
    tls_key_path: Path | None = None
    request_max_bytes: int = MAX_REQUEST_BYTES
    request_max_lifetime_seconds: int = 900
    worker_timeout_seconds: float = 300.0
    retry_after_seconds: int = 15
    max_open_total: int = 16
    max_open_per_credential: int = 4
    tombstone_retention_seconds: int = 86_400
    maintenance_interval_seconds: float = 30.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "ollama_base_url", _loopback_worker_url(self.ollama_base_url))
        if not self.ollama_model.strip() or len(self.ollama_model) > 256:
            raise ConfigurationError("Ollama model identifier is invalid")
        if not self.deployment_id or len(self.deployment_id) > 128:
            raise ConfigurationError("deployment id is invalid")
        try:
            bind_address = ipaddress.ip_address(self.bind_host)
        except ValueError as exc:
            raise ConfigurationError("bind host must be an IP address") from exc
        tls_paths = (self.tls_certificate_path, self.tls_key_path)
        if (tls_paths[0] is None) != (tls_paths[1] is None):
            raise ConfigurationError("TLS certificate and key must be configured together")
        if not bind_address.is_loopback:
            if (
                not any(bind_address in network for network in PRIVATE_BIND_NETWORKS)
                or bind_address.is_unspecified
                or bind_address.is_multicast
                or bind_address.is_link_local
                or bind_address.is_reserved
            ):
                raise ConfigurationError(
                    "non-loopback bind host must be a concrete private address"
                )
            if tls_paths[0] is None:
                raise ConfigurationError("non-loopback bind requires TLS")
        if tls_paths[0] is not None and tls_paths[1] is not None:
            _validate_tls_file(tls_paths[0], "TLS certificate", owner_private=False)
            _validate_tls_file(tls_paths[1], "TLS key", owner_private=True)
        if not 1 <= self.bind_port <= 65_535:
            raise ConfigurationError("bind port is invalid")
        if not 1 <= self.request_max_bytes <= MAX_REQUEST_BYTES:
            raise ConfigurationError("request byte limit is invalid")
        if not 1 <= self.request_max_lifetime_seconds <= 86_400:
            raise ConfigurationError("request lifetime is invalid")
        if not 1 <= self.worker_timeout_seconds <= 3_600:
            raise ConfigurationError("worker timeout is invalid")
        if not 1 <= self.retry_after_seconds <= 86_400:
            raise ConfigurationError("retry interval is invalid")
        if not 1 <= self.max_open_per_credential <= self.max_open_total <= 10_000:
            raise ConfigurationError("admission limits are invalid")
        if not 1 <= self.tombstone_retention_seconds <= 31_536_000:
            raise ConfigurationError("tombstone retention is invalid")
        if not 0.01 <= self.maintenance_interval_seconds <= 3_600:
            raise ConfigurationError("maintenance interval is invalid")

    @classmethod
    def from_env(cls) -> Settings:
        required = {
            "database_path": "GATEWAY_DATABASE_FILE",
            "credentials_path": "GATEWAY_CREDENTIALS_FILE",
            "encryption_key_path": "GATEWAY_ENCRYPTION_KEY_FILE",
            "ollama_base_url": "GATEWAY_OLLAMA_URL",
            "ollama_model": "GATEWAY_OLLAMA_MODEL",
            "deployment_id": "GATEWAY_DEPLOYMENT_ID",
        }
        values: dict[str, object] = {}
        for field, variable in required.items():
            value = os.environ.get(variable)
            if not value:
                raise ConfigurationError(f"{variable} is required")
            values[field] = Path(value) if field.endswith("_path") else value
        values["bind_host"] = os.environ.get("GATEWAY_BIND_HOST", "127.0.0.1")
        values["bind_port"] = _environment_int("GATEWAY_BIND_PORT", 8787)
        certificate = os.environ.get("GATEWAY_TLS_CERTIFICATE_FILE")
        key = os.environ.get("GATEWAY_TLS_KEY_FILE")
        values["tls_certificate_path"] = Path(certificate) if certificate else None
        values["tls_key_path"] = Path(key) if key else None
        values["maintenance_interval_seconds"] = _environment_float(
            "GATEWAY_MAINTENANCE_INTERVAL_SECONDS", 30.0
        )
        return cls(**values)  # type: ignore[arg-type]


def _environment_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def _environment_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
