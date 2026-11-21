"""SQLite persistence layer.

Only the Python standard library is used - the database is a single file
(``DATABASE_URL``) that lives on a Docker volume, so chat history survives
container restarts. The schema is created on startup.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,              -- 'user' or 'assistant'
    content     TEXT NOT NULL,
    metadata    TEXT,                       -- JSON string, assistant messages only
    created_at  TEXT NOT NULL               -- server time, ISO-8601
);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages (session_id, id);

CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    system_prompt TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""


def utcnow_iso() -> str:
    """Server time as ISO-8601 with a Z suffix."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class Database:
    def __init__(self, path: str) -> None:
        self.path = path

    # ---------------------------------------------------------------- plumbing
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=30000")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def ping(self) -> bool:
        try:
            with self.connect() as conn:
                conn.execute("SELECT 1 FROM messages LIMIT 1").fetchone()
            return True
        except Exception:
            return False

    # ---------------------------------------------------------------- sessions
    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT session_id, system_prompt, created_at, updated_at"
                " FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_system_prompt(self, session_id: str) -> str | None:
        session = self.get_session(session_id)
        return session["system_prompt"] if session else None

    def session_exists(self, session_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?"
                " UNION ALL SELECT 1 FROM messages WHERE session_id = ? LIMIT 1",
                (session_id, session_id),
            ).fetchone()
        return row is not None

    def upsert_session(self, session_id: str, system_prompt: str | None) -> None:
        """Create the session row, or update its system prompt when given one."""
        now = utcnow_iso()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, system_prompt, created_at, updated_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(session_id) DO UPDATE SET"
                "   system_prompt = COALESCE(excluded.system_prompt, sessions.system_prompt),"
                "   updated_at = excluded.updated_at",
                (session_id, system_prompt, now, now),
            )

    # ---------------------------------------------------------------- messages
    def get_history(self, session_id: str, limit: int | None = None) -> list[dict[str, str]]:
        """Chat history for the LLM: role/content pairs, oldest first."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE session_id = ?"
                " ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        history = [{"role": row["role"], "content": row["content"]} for row in rows]
        if limit is not None and limit > 0 and len(history) > limit:
            history = history[-limit:]
        return history

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Stored history for the API, oldest first, with parsed metadata."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, role, content, metadata, created_at FROM messages"
                " WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        messages: list[dict[str, Any]] = []
        for row in rows:
            item: dict[str, Any] = {
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
            }
            if row["metadata"]:
                try:
                    item["metadata"] = json.loads(row["metadata"])
                except (TypeError, ValueError):
                    item["metadata"] = None
            messages.append(item)
        return messages

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    session_id,
                    role,
                    content,
                    json.dumps(metadata) if metadata is not None else None,
                    utcnow_iso(),
                ),
            )
        return int(cursor.lastrowid or 0)

    def add_exchange(
        self,
        session_id: str,
        user_message: str,
        assistant_reply: str,
        metadata: dict[str, Any],
        system_prompt: str | None = None,
    ) -> None:
        """Persist one successful turn (user + assistant) in a single transaction."""
        now = utcnow_iso()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, system_prompt, created_at, updated_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(session_id) DO UPDATE SET"
                "   system_prompt = COALESCE(excluded.system_prompt, sessions.system_prompt),"
                "   updated_at = excluded.updated_at",
                (session_id, system_prompt, now, now),
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at)"
                " VALUES (?, 'user', ?, NULL, ?)",
                (session_id, user_message, now),
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at)"
                " VALUES (?, 'assistant', ?, ?, ?)",
                (session_id, assistant_reply, json.dumps(metadata), utcnow_iso()),
            )

    def count_messages(self, session_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row["n"]) if row else 0
