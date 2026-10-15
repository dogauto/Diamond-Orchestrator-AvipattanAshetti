"""SQLite-backed session registry for CLI agent sessions.

Extends the existing EventStore pattern with session registration,
role/capability tracking, and task ownership.
"""

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..domain import Provider, Role
from ..errors import NotFoundError, StorageError, ValidationError
from ..paths import resolve_storage_path, storage_stat
from ..validation import bounded_int, command_id, exact_keys, parse_canonical_json, text, utc_now


@dataclass(frozen=True)
class SessionRecord:
    """A registered CLI agent session."""
    session_id: str
    agent_name: str
    role: Role
    provider: Provider
    tmux_session: str
    tmux_pane: str
    capabilities: tuple[str, ...]
    owned_task_ids: tuple[str, ...]
    status: str  # ACTIVE, IDLE, BUSY, TERMINATED
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        import json
        return json.dumps({
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "role": self.role.value,
            "provider": self.provider.value,
            "tmux_session": self.tmux_session,
            "tmux_pane": self.tmux_pane,
            "capabilities": list(self.capabilities),
            "owned_task_ids": list(self.owned_task_ids),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }, separators=(",", ":"))

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SessionRecord":
        import json
        return cls(
            session_id=row["session_id"],
            agent_name=row["agent_name"],
            role=Role(row["role"]),
            provider=Provider(row["provider"]),
            tmux_session=row["tmux_session"],
            tmux_pane=row["tmux_pane"],
            capabilities=tuple(json.loads(row["capabilities"])),
            owned_task_ids=tuple(json.loads(row["owned_task_ids"])),
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )


class SessionRegistry:
    """SQLite-backed session registry with CAS updates."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        agent_name TEXT NOT NULL,
        role TEXT NOT NULL,
        provider TEXT NOT NULL,
        tmux_session TEXT NOT NULL,
        tmux_pane TEXT NOT NULL,
        capabilities TEXT NOT NULL DEFAULT '[]',
        owned_task_ids TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'ACTIVE',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}',
        revision INTEGER NOT NULL DEFAULT 1
    );

    CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent_name);
    CREATE INDEX IF NOT EXISTS idx_sessions_role ON sessions(role);
    CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
    """

    def __init__(self, database: Path, busy_timeout_ms: int = 5000) -> None:
        if not isinstance(database, Path) or not database.is_absolute():
            raise ValidationError("database must be an absolute pathlib.Path")
        bounded_int(busy_timeout_ms, "busy_timeout_ms", 1, 30_000)

        self.database = resolve_storage_path(database)
        self.busy_timeout_ms = busy_timeout_ms

        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connection(write=True) as conn:
            conn.executescript(self.SCHEMA)

    def _database_present(self) -> bool:
        info = storage_stat(self.database)
        return info is not None and info.st_size > 0

    @contextmanager
    def _connection(self, write: bool) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            if write:
                connection = sqlite3.connect(self.database, isolation_level=None, timeout=self.busy_timeout_ms / 1000)
            else:
                connection = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True,
                                             isolation_level=None, timeout=self.busy_timeout_ms / 1000)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            if write:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
            else:
                connection.execute("PRAGMA query_only=ON")
                connection.execute("BEGIN")
            yield connection
            if write:
                connection.commit()
        except (sqlite3.Error, OSError) as exc:
            raise StorageError(f"session registry operation failed: {exc}") from exc
        finally:
            if connection is not None:
                try:
                    if connection.in_transaction:
                        connection.rollback()
                finally:
                    connection.close()

    def register(
        self,
        agent_name: str,
        role: Role,
        provider: Provider,
        tmux_session: str,
        tmux_pane: str,
        capabilities: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> SessionRecord:
        """Register a new agent session."""
        text(agent_name, "agent_name", 256)
        text(tmux_session, "tmux_session", 256)
        text(tmux_pane, "tmux_pane", 256)
        if not isinstance(role, Role) or not isinstance(provider, Provider):
            raise ValidationError("role and provider must be enum values")

        session_id = f"{agent_name}-{int(time.time() * 1000)}"
        now = utc_now()

        record = SessionRecord(
            session_id=session_id,
            agent_name=agent_name,
            role=role,
            provider=provider,
            tmux_session=tmux_session,
            tmux_pane=tmux_pane,
            capabilities=capabilities,
            owned_task_ids=(),
            status="ACTIVE",
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )

        import json
        with self._connection(write=True) as conn:
            # Check for duplicate agent_name (should be unique)
            existing = conn.execute("SELECT session_id FROM sessions WHERE agent_name=?", (agent_name,)).fetchone()
            if existing:
                raise ValidationError(f"agent {agent_name} already registered as {existing['session_id']}")

            conn.execute("""
                INSERT INTO sessions
                (session_id, agent_name, role, provider, tmux_session, tmux_pane,
                 capabilities, owned_task_ids, status, created_at, updated_at, metadata, revision)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.session_id, record.agent_name, record.role.value, record.provider.value,
                record.tmux_session, record.tmux_pane,
                json.dumps(list(record.capabilities)), json.dumps(list(record.owned_task_ids)),
                record.status, record.created_at, record.updated_at,
                json.dumps(record.metadata), 1
            ))

        return record

    def get(self, session_id: str) -> SessionRecord:
        """Get a session by ID."""
        text(session_id, "session_id", 256)
        with self._connection(write=False) as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"session {session_id} not found")
            return SessionRecord.from_row(row)

    def get_by_agent(self, agent_name: str) -> SessionRecord:
        """Get a session by agent name."""
        text(agent_name, "agent_name", 256)
        with self._connection(write=False) as conn:
            row = conn.execute("SELECT * FROM sessions WHERE agent_name=?", (agent_name,)).fetchone()
            if row is None:
                raise NotFoundError(f"session for agent {agent_name} not found")
            return SessionRecord.from_row(row)

    def list(self, status: str | None = None, role: Role | None = None) -> tuple[SessionRecord, ...]:
        """List sessions with optional filters."""
        with self._connection(write=False) as conn:
            query = "SELECT * FROM sessions"
            params: list[Any] = []
            conditions = []
            if status is not None:
                conditions.append("status=?")
                params.append(status)
            if role is not None:
                conditions.append("role=?")
                params.append(role.value)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY created_at"
            rows = conn.execute(query, params).fetchall()
            return tuple(SessionRecord.from_row(row) for row in rows)

    def update_status(self, session_id: str, new_status: str, expected_revision: int) -> SessionRecord:
        """Update session status with CAS."""
        text(session_id, "session_id", 256)
        text(new_status, "status", 64)
        bounded_int(expected_revision, "expected_revision", 1, 2**63 - 1)

        with self._connection(write=True) as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id=? AND revision=?", (session_id, expected_revision)).fetchone()
            if row is None:
                # Check if exists but revision mismatch
                existing = conn.execute("SELECT revision FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                if existing:
                    raise ValidationError(f"expected revision {expected_revision}, found {existing['revision']}")
                raise NotFoundError(f"session {session_id} not found")

            record = SessionRecord.from_row(row)
            updated = SessionRecord(
                session_id=record.session_id,
                agent_name=record.agent_name,
                role=record.role,
                provider=record.provider,
                tmux_session=record.tmux_session,
                tmux_pane=record.tmux_pane,
                capabilities=record.capabilities,
                owned_task_ids=record.owned_task_ids,
                status=new_status,
                created_at=record.created_at,
                updated_at=utc_now(),
                metadata=record.metadata,
            )

            import json
            conn.execute("""
                UPDATE sessions SET status=?, updated_at=?, revision=revision+1
                WHERE session_id=? AND revision=?
            """, (new_status, updated.updated_at, session_id, expected_revision))

            return updated

    def claim_task(self, session_id: str, task_id: str, expected_revision: int) -> SessionRecord:
        """Add a task to a session's owned tasks."""
        text(task_id, "task_id", 4096)
        text(session_id, "session_id", 256)
        bounded_int(expected_revision, "expected_revision", 1, 2**63 - 1)

        with self._connection(write=True) as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id=? AND revision=?", (session_id, expected_revision)).fetchone()
            if row is None:
                existing = conn.execute("SELECT revision FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                if existing:
                    raise ValidationError(f"expected revision {expected_revision}, found {existing['revision']}")
                raise NotFoundError(f"session {session_id} not found")

            record = SessionRecord.from_row(row)
            if task_id in record.owned_task_ids:
                return record

            new_owned = record.owned_task_ids + (task_id,)
            updated = SessionRecord(
                session_id=record.session_id,
                agent_name=record.agent_name,
                role=record.role,
                provider=record.provider,
                tmux_session=record.tmux_session,
                tmux_pane=record.tmux_pane,
                capabilities=record.capabilities,
                owned_task_ids=new_owned,
                status=record.status,
                created_at=record.created_at,
                updated_at=utc_now(),
                metadata=record.metadata,
            )

            import json
            conn.execute("""
                UPDATE sessions SET owned_task_ids=?, updated_at=?, revision=revision+1
                WHERE session_id=? AND revision=?
            """, (json.dumps(list(new_owned)), updated.updated_at, session_id, expected_revision))

            return updated

    def release_task(self, session_id: str, task_id: str, expected_revision: int) -> SessionRecord:
        """Remove a task from a session's owned tasks."""
        text(task_id, "task_id", 4096)
        text(session_id, "session_id", 256)
        bounded_int(expected_revision, "expected_revision", 1, 2**63 - 1)

        with self._connection(write=True) as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id=? AND revision=?", (session_id, expected_revision)).fetchone()
            if row is None:
                existing = conn.execute("SELECT revision FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                if existing:
                    raise ValidationError(f"expected revision {expected_revision}, found {existing['revision']}")
                raise NotFoundError(f"session {session_id} not found")

            record = SessionRecord.from_row(row)
            if task_id not in record.owned_task_ids:
                return record

            new_owned = tuple(t for t in record.owned_task_ids if t != task_id)
            updated = SessionRecord(
                session_id=record.session_id,
                agent_name=record.agent_name,
                role=record.role,
                provider=record.provider,
                tmux_session=record.tmux_session,
                tmux_pane=record.tmux_pane,
                capabilities=record.capabilities,
                owned_task_ids=new_owned,
                status=record.status,
                created_at=record.created_at,
                updated_at=utc_now(),
                metadata=record.metadata,
            )

            import json
            conn.execute("""
                UPDATE sessions SET owned_task_ids=?, updated_at=?, revision=revision+1
                WHERE session_id=? AND revision=?
            """, (json.dumps(list(new_owned)), updated.updated_at, session_id, expected_revision))

            return updated

    def update_capabilities(self, session_id: str, capabilities: tuple[str, ...], expected_revision: int) -> SessionRecord:
        """Update session capabilities."""
        bounded_int(expected_revision, "expected_revision", 1, 2**63 - 1)

        with self._connection(write=True) as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id=? AND revision=?", (session_id, expected_revision)).fetchone()
            if row is None:
                existing = conn.execute("SELECT revision FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                if existing:
                    raise ValidationError(f"expected revision {expected_revision}, found {existing['revision']}")
                raise NotFoundError(f"session {session_id} not found")

            record = SessionRecord.from_row(row)
            updated = SessionRecord(
                session_id=record.session_id,
                agent_name=record.agent_name,
                role=record.role,
                provider=record.provider,
                tmux_session=record.tmux_session,
                tmux_pane=record.tmux_pane,
                capabilities=capabilities,
                owned_task_ids=record.owned_task_ids,
                status=record.status,
                created_at=record.created_at,
                updated_at=utc_now(),
                metadata=record.metadata,
            )

            import json
            conn.execute("""
                UPDATE sessions SET capabilities=?, updated_at=?, revision=revision+1
                WHERE session_id=? AND revision=?
            """, (json.dumps(list(capabilities)), updated.updated_at, session_id, expected_revision))

            return updated

    def terminate(self, session_id: str, expected_revision: int) -> SessionRecord:
        """Mark a session as terminated."""
        return self.update_status(session_id, "TERMINATED", expected_revision)

    def _validate_task_id(self, task_id_str: str) -> None:
        try:
            task_id(task_id_str)
        except ValidationError as exc:
            raise ValidationError(f"invalid task_id: {exc}") from exc