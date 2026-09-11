from __future__ import annotations

import base64
import os
import sqlite3
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import ClassVar, Literal

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .contracts import InferenceRequest

RequestState = Literal[
    "reserved",
    "in_progress",
    "completed",
    "failed",
    "ambiguous",
    "expired",
    "acknowledged",
]


class StoreError(RuntimeError):
    """A durable request transition was rejected."""


class RequestForbidden(StoreError):
    pass


class RequestIdentityConflict(StoreError):
    pass


class RequestCapacityLimited(StoreError):
    pass


class RequestExpired(StoreError):
    pass


class RequestUnknown(StoreError):
    pass


class RequestNotTerminal(StoreError):
    pass


class AcknowledgementConflict(StoreError):
    pass


@dataclass(frozen=True)
class RequestRecord:
    request_id: str
    credential_hash: str
    request_digest: str
    task_id: str
    task_version: int
    expires_at: datetime
    state: RequestState
    attempt_id: str | None
    output_media_type: str | None
    output_nonce: bytes | None
    output_ciphertext: bytes | None
    producing_deployment_id: str | None
    producing_task_policy_version: int | None
    error_code: str | None
    error_retryable: bool | None
    error_retry_after_seconds: int | None
    acknowledgement_disposition: str | None


class ResultCipher:
    def __init__(self, key: bytes):
        if len(key) != 32:
            raise StoreError("encryption key must decode to 32 bytes")
        self._cipher = AESGCM(key)

    @classmethod
    def from_file(cls, path: Path) -> ResultCipher:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or not 1 <= metadata.st_size <= 128:
                    raise StoreError("encryption key file is invalid")
                if os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077:
                    raise StoreError("encryption key file must be owner-private")
                encoded = stream.read(129).strip()
        except StoreError:
            raise
        except OSError as exc:
            raise StoreError("encryption key file is unavailable") from exc
        try:
            key = base64.b64decode(encoded, altchars=b"-_", validate=True)
        except (ValueError, TypeError) as exc:
            raise StoreError("encryption key file is invalid") from exc
        return cls(key)

    @staticmethod
    def _aad(record: RequestRecord) -> bytes:
        return (f"{record.request_id}\n{record.credential_hash}\n{record.request_digest}").encode(
            "ascii"
        )

    def encrypt(self, record: RequestRecord, plaintext: str) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext.encode("utf-8"), self._aad(record))
        return nonce, ciphertext

    def decrypt(self, record: RequestRecord) -> str:
        if record.output_nonce is None or record.output_ciphertext is None:
            raise StoreError("completed result has no encrypted output")
        try:
            plaintext = self._cipher.decrypt(
                record.output_nonce,
                record.output_ciphertext,
                self._aad(record),
            )
            return plaintext.decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise StoreError("encrypted output failed integrity verification") from exc


class RequestStore:
    SCHEMA_VERSION = 1
    SCHEMA_COLUMNS: ClassVar[dict[str, set[str]]] = {
        "schema_metadata": {"singleton", "version"},
        "inference_requests": {
            "request_id",
            "credential_hash",
            "request_digest",
            "task_id",
            "task_version",
            "expires_at",
            "state",
            "attempt_id",
            "output_media_type",
            "output_nonce",
            "output_ciphertext",
            "producing_deployment_id",
            "producing_task_policy_version",
            "error_code",
            "error_retryable",
            "error_retry_after_seconds",
            "acknowledgement_disposition",
            "created_at",
            "updated_at",
            "acknowledged_at",
        },
    }

    def __init__(
        self,
        path: Path,
        cipher: ResultCipher,
        *,
        max_open_total: int,
        max_open_per_credential: int,
        tombstone_retention_seconds: int,
    ):
        self.path = path
        self.cipher = cipher
        self.max_open_total = max_open_total
        self.max_open_per_credential = max_open_per_credential
        self.tombstone_retention_seconds = tombstone_retention_seconds

    def initialize(self, now: datetime) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._connect(enable_wal=False) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                }
                if not tables:
                    self._create_schema(connection)
                elif tables != {"schema_metadata", "inference_requests"}:
                    raise StoreError("database schema is incomplete or unversioned")
                version_rows = connection.execute(
                    "SELECT version FROM schema_metadata WHERE singleton = 1"
                ).fetchall()
                if len(version_rows) != 1 or type(version_rows[0][0]) is not int:
                    raise StoreError("database schema metadata is invalid")
                version = version_rows[0][0]
                if version != self.SCHEMA_VERSION:
                    raise StoreError(
                        f"database schema version {version} is not supported by "
                        f"{self.SCHEMA_VERSION}"
                    )
                self._validate_schema_columns(connection)
                connection.execute(
                    """
                    UPDATE inference_requests
                    SET state = 'ambiguous', updated_at = ?
                    WHERE state = 'in_progress'
                    """,
                    (_timestamp(now),),
                )
                self._cleanup(connection, now)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("PRAGMA journal_mode = WAL")
        if os.name == "posix":
            self.path.chmod(0o600)

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    version INTEGER NOT NULL
                )
            """
        )
        connection.execute("INSERT INTO schema_metadata (singleton, version) VALUES (1, 1)")
        connection.execute(
            """
                CREATE TABLE IF NOT EXISTS inference_requests (
                    request_id TEXT PRIMARY KEY,
                    credential_hash TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    task_version INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN (
                        'reserved', 'in_progress', 'completed', 'failed',
                        'ambiguous', 'expired', 'acknowledged'
                    )),
                    attempt_id TEXT,
                    output_media_type TEXT,
                    output_nonce BLOB,
                    output_ciphertext BLOB,
                    producing_deployment_id TEXT,
                    producing_task_policy_version INTEGER
                        CHECK (producing_task_policy_version >= 1),
                    error_code TEXT,
                    error_retryable INTEGER,
                    error_retry_after_seconds INTEGER,
                    acknowledgement_disposition TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    acknowledged_at TEXT
                )
            """
        )
        connection.execute(
            """
                CREATE INDEX IF NOT EXISTS inference_requests_owner_state
                    ON inference_requests (credential_hash, state)
            """
        )

    @classmethod
    def _validate_schema_columns(cls, connection: sqlite3.Connection) -> None:
        for table, expected in cls.SCHEMA_COLUMNS.items():
            actual = {
                row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if actual != expected:
                raise StoreError("database schema columns do not match the supported version")

    def admit(
        self,
        request: InferenceRequest,
        credential_hash: str,
        request_digest: str,
        now: datetime,
    ) -> RequestRecord:
        with self._transaction() as connection:
            self._cleanup(connection, now)
            existing = self._select(connection, request.request_id)
            if existing is not None:
                self._authorize_existing(existing, credential_hash, request_digest)
                return existing
            if request.expires_at <= now:
                raise RequestExpired("request has expired")
            open_states = ("reserved", "in_progress", "completed", "ambiguous")
            placeholders = ",".join("?" for _ in open_states)
            total = connection.execute(
                f"SELECT COUNT(*) FROM inference_requests WHERE state IN ({placeholders})",
                open_states,
            ).fetchone()[0]
            per_owner = connection.execute(
                f"""SELECT COUNT(*) FROM inference_requests
                    WHERE credential_hash = ? AND state IN ({placeholders})""",
                (credential_hash, *open_states),
            ).fetchone()[0]
            if total >= self.max_open_total or per_owner >= self.max_open_per_credential:
                raise RequestCapacityLimited("durable admission capacity is full")
            timestamp = _timestamp(now)
            connection.execute(
                """
                INSERT INTO inference_requests (
                    request_id, credential_hash, request_digest, task_id, task_version,
                    expires_at, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'reserved', ?, ?)
                """,
                (
                    request.request_id,
                    credential_hash,
                    request_digest,
                    request.task.id,
                    request.task.version,
                    request.request_expires_at,
                    timestamp,
                    timestamp,
                ),
            )
            record = self._select(connection, request.request_id)
            assert record is not None
            return record

    def get_owned(
        self,
        request_id: str,
        credential_hash: str,
        request_digest: str | None,
        now: datetime,
    ) -> RequestRecord:
        with self._transaction() as connection:
            self._cleanup(connection, now)
            record = self._select(connection, request_id)
            if record is None:
                raise RequestUnknown("request is unknown")
            self._authorize_existing(record, credential_hash, request_digest)
            return record

    def mark_in_progress(self, request_id: str, attempt_id: str, now: datetime) -> RequestRecord:
        with self._transaction() as connection:
            self._cleanup(connection, now)
            cursor = connection.execute(
                """
                UPDATE inference_requests
                SET state = 'in_progress', attempt_id = ?, updated_at = ?
                WHERE request_id = ? AND state = 'reserved'
                """,
                (attempt_id, _timestamp(now), request_id),
            )
            if cursor.rowcount != 1:
                raise StoreError("request is not reserved for dispatch")
            record = self._select(connection, request_id)
            assert record is not None
            return record

    def reset_reserved(self, request_id: str, attempt_id: str, now: datetime) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE inference_requests
                SET state = 'reserved', attempt_id = NULL, updated_at = ?
                WHERE request_id = ? AND state = 'in_progress' AND attempt_id = ?
                """,
                (_timestamp(now), request_id, attempt_id),
            )

    def mark_ambiguous(self, request_id: str, attempt_id: str, now: datetime) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE inference_requests
                SET state = 'ambiguous', updated_at = ?
                WHERE request_id = ? AND state = 'in_progress' AND attempt_id = ?
                """,
                (_timestamp(now), request_id, attempt_id),
            )

    def mark_failed(
        self,
        request_id: str,
        attempt_id: str,
        code: str,
        retryable: bool,
        retry_after_seconds: int | None,
        now: datetime,
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE inference_requests
                SET state = 'failed', error_code = ?, error_retryable = ?,
                    error_retry_after_seconds = ?, updated_at = ?
                WHERE request_id = ? AND state = 'in_progress' AND attempt_id = ?
                """,
                (
                    code,
                    int(retryable),
                    retry_after_seconds,
                    _timestamp(now),
                    request_id,
                    attempt_id,
                ),
            )

    def complete(
        self,
        request_id: str,
        attempt_id: str,
        media_type: str,
        content: str,
        *,
        deployment_id: str,
        task_policy_version: int,
        now: datetime,
    ) -> bool:
        with self._transaction() as connection:
            self._cleanup(connection, now)
            record = self._select(connection, request_id)
            if record is None or record.state == "expired":
                return False
            if record.state != "in_progress" or record.attempt_id != attempt_id:
                raise StoreError("worker completion does not own the active attempt")
            nonce, ciphertext = self.cipher.encrypt(record, content)
            cursor = connection.execute(
                """
                UPDATE inference_requests
                SET state = 'completed', output_media_type = ?, output_nonce = ?,
                    output_ciphertext = ?, producing_deployment_id = ?,
                    producing_task_policy_version = ?, updated_at = ?
                WHERE request_id = ? AND state = 'in_progress' AND attempt_id = ?
                """,
                (
                    media_type,
                    nonce,
                    ciphertext,
                    deployment_id,
                    task_policy_version,
                    _timestamp(now),
                    request_id,
                    attempt_id,
                ),
            )
            return int(cursor.rowcount) == 1

    def maintain(self, now: datetime) -> None:
        with self._transaction() as connection:
            self._cleanup(connection, now)

    def output(self, record: RequestRecord) -> str:
        if record.state != "completed":
            raise RequestNotTerminal("request does not have a completed result")
        return self.cipher.decrypt(record)

    def acknowledge(
        self,
        request_id: str,
        credential_hash: str,
        disposition: str,
        now: datetime,
    ) -> RequestRecord:
        with self._transaction() as connection:
            self._cleanup(connection, now)
            record = self._select(connection, request_id)
            if record is None:
                raise RequestUnknown("request is unknown")
            self._authorize_existing(record, credential_hash, None)
            if record.state == "acknowledged":
                if record.acknowledgement_disposition != disposition:
                    raise AcknowledgementConflict("acknowledgement disposition conflicts")
                return record
            if record.state == "expired":
                raise RequestExpired("request has expired")
            if record.state != "completed":
                raise RequestNotTerminal("request result is not terminal")
            connection.execute(
                """
                UPDATE inference_requests
                SET state = 'acknowledged', acknowledgement_disposition = ?,
                    output_media_type = NULL, output_nonce = NULL,
                    output_ciphertext = NULL, updated_at = ?, acknowledged_at = ?
                WHERE request_id = ? AND state = 'completed'
                """,
                (disposition, _timestamp(now), _timestamp(now), request_id),
            )
            acknowledged = self._select(connection, request_id)
            assert acknowledged is not None
            return acknowledged

    def raw_persisted_values(self, request_id: str) -> tuple[bytes | None, bytes | None]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT output_nonce, output_ciphertext "
                "FROM inference_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        if row is None:
            raise RequestUnknown("request is unknown")
        return row[0], row[1]

    def _authorize_existing(
        self,
        record: RequestRecord,
        credential_hash: str,
        request_digest: str | None,
    ) -> None:
        if record.credential_hash != credential_hash:
            raise RequestForbidden("request belongs to another credential")
        if request_digest is not None and record.request_digest != request_digest:
            raise RequestIdentityConflict("request identity was reused with different content")

    def _cleanup(self, connection: sqlite3.Connection, now: datetime) -> None:
        timestamp = _timestamp(now)
        connection.execute(
            """
            UPDATE inference_requests
            SET state = 'expired', output_media_type = NULL, output_nonce = NULL,
                output_ciphertext = NULL, updated_at = ?
            WHERE state NOT IN ('acknowledged', 'expired') AND expires_at <= ?
            """,
            (timestamp, timestamp),
        )
        cutoff = _timestamp(now - timedelta(seconds=self.tombstone_retention_seconds))
        connection.execute(
            """
            DELETE FROM inference_requests
            WHERE state IN ('acknowledged', 'expired') AND updated_at < ?
            """,
            (cutoff,),
        )

    def _select(self, connection: sqlite3.Connection, request_id: str) -> RequestRecord | None:
        row = connection.execute(
            "SELECT * FROM inference_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        if row is None:
            return None
        retryable = row["error_retryable"]
        return RequestRecord(
            request_id=row["request_id"],
            credential_hash=row["credential_hash"],
            request_digest=row["request_digest"],
            task_id=row["task_id"],
            task_version=row["task_version"],
            expires_at=_parse_timestamp(row["expires_at"]),
            state=row["state"],
            attempt_id=row["attempt_id"],
            output_media_type=row["output_media_type"],
            output_nonce=row["output_nonce"],
            output_ciphertext=row["output_ciphertext"],
            producing_deployment_id=row["producing_deployment_id"],
            producing_task_policy_version=row["producing_task_policy_version"],
            error_code=row["error_code"],
            error_retryable=bool(retryable) if retryable is not None else None,
            error_retry_after_seconds=row["error_retry_after_seconds"],
            acknowledgement_disposition=row["acknowledgement_disposition"],
        )

    def _connect(self, *, enable_wal: bool = True) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if enable_wal:
            connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _transaction(self) -> _Transaction:
        return _Transaction(self._connect())


class _Transaction:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def __enter__(self) -> sqlite3.Connection:
        self.connection.execute("BEGIN IMMEDIATE")
        return self.connection

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self.connection.execute("ROLLBACK" if exc_type is not None else "COMMIT")
        finally:
            self.connection.close()


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
