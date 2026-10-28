"""Small SQLite repository using only the standard library.

The repository is intentionally synchronous: each operation is a short transaction,
which keeps the service dependency-light and works well with FastAPI's threadpool.
"""

import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any

from .models import Action, Candidate, Connection, Event, now_iso


class SQLiteStore:
    def __init__(self, database_url: str):
        prefix = "sqlite:///"
        if not database_url.startswith(prefix):
            raise ValueError("Only sqlite:/// database URLs are supported")
        raw_path = database_url[len(prefix) :]
        self.path = ":memory:" if raw_path in ("", ":memory:") else raw_path
        if self.path != ":memory:":
            path = Path(self.path)
            if path.parent and str(path.parent) != ".":
                path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidates (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, content TEXT NOT NULL,
                    provider TEXT NOT NULL, model TEXT NOT NULL, score REAL, selected INTEGER NOT NULL,
                    created_at TEXT NOT NULL, platform TEXT, content_id TEXT, title TEXT,
                    summary TEXT, url TEXT, metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, type TEXT NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                    idempotency_key TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, type TEXT NOT NULL,
                    state TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS connections (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                    config TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
                );
                """
            )
            # Existing local databases from the first preview are upgraded in
            # place. SQLite ignores duplicate additions, so this remains safe
            # for fresh installs and restarts.
            for table, column, definition in (
                ("candidates", "platform", "TEXT"),
                ("candidates", "content_id", "TEXT"),
                ("candidates", "title", "TEXT"),
                ("candidates", "summary", "TEXT"),
                ("candidates", "url", "TEXT"),
                ("candidates", "metadata", "TEXT NOT NULL DEFAULT '{}'"),
                ("actions", "idempotency_key", "TEXT"),
            ):
                columns = {row[1] for row in self._connection.execute(f"PRAGMA table_info({table})").fetchall()}
                if column not in columns:
                    self._connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def ensure_conversation(self, conversation_id: str) -> str:
        timestamp = now_iso()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO conversations (id, created_at, updated_at) VALUES (?, ?, ?)",
                (conversation_id, timestamp, timestamp),
            )
            self._connection.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?", (timestamp, conversation_id)
            )
        return conversation_id

    def add_message(self, conversation_id: str, role: str, content: str) -> None:
        self.ensure_conversation(conversation_id)
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, role, content, now_iso()),
            )

    def get_messages(self, conversation_id: str, limit: int = 50) -> list[dict[str, str]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def add_candidate(self, candidate: Candidate) -> Candidate:
        self.ensure_conversation(candidate.conversation_id)
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO candidates
                (id, conversation_id, content, provider, model, score, selected, created_at,
                 platform, content_id, title, summary, url, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (candidate.id, candidate.conversation_id, candidate.content, candidate.provider,
                 candidate.model, candidate.score, int(candidate.selected), candidate.created_at,
                 candidate.platform, candidate.content_id, candidate.title, candidate.summary,
                 candidate.url, json.dumps(candidate.metadata, ensure_ascii=False)),
            )
        return candidate

    def list_candidates(self, conversation_id: str | None = None, limit: int = 100) -> list[Candidate]:
        query = "SELECT * FROM candidates"
        args: list[Any] = []
        if conversation_id:
            query += " WHERE conversation_id=?"
            args.append(conversation_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._connection.execute(query, args).fetchall()
        return [Candidate(id=r["id"], conversation_id=r["conversation_id"], content=r["content"],
                          provider=r["provider"], model=r["model"], score=r["score"],
                          selected=bool(r["selected"]), created_at=r["created_at"],
                          platform=r["platform"], content_id=r["content_id"], title=r["title"],
                          summary=r["summary"], url=r["url"],
                          metadata=json.loads(r["metadata"] or "{}")) for r in rows]

    def update_candidate_selected(self, candidate_id: str, selected: bool = True) -> Candidate | None:
        with self._lock, self._connection:
            self._connection.execute("UPDATE candidates SET selected=? WHERE id=?", (int(selected), candidate_id))
            row = self._connection.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        if row is None:
            return None
        return Candidate(id=row["id"], conversation_id=row["conversation_id"], content=row["content"],
                         provider=row["provider"], model=row["model"], score=row["score"],
                         selected=bool(row["selected"]), created_at=row["created_at"],
                         platform=row["platform"], content_id=row["content_id"], title=row["title"],
                         summary=row["summary"], url=row["url"],
                         metadata=json.loads(row["metadata"] or "{}"))

    def add_action(self, action: Action) -> Action:
        self.ensure_conversation(action.conversation_id)
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO actions (id, conversation_id, type, payload, status, created_at, idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (action.id, action.conversation_id, action.type, json.dumps(action.payload),
                 action.status, action.created_at, action.idempotency_key),
            )
        return action

    def list_actions(self, conversation_id: str | None = None, limit: int = 100) -> list[Action]:
        query = "SELECT * FROM actions"
        args: list[Any] = []
        if conversation_id:
            query += " WHERE conversation_id=?"
            args.append(conversation_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._connection.execute(query, args).fetchall()
        return [Action(id=r["id"], conversation_id=r["conversation_id"], type=r["type"],
                       payload=json.loads(r["payload"]), status=r["status"], created_at=r["created_at"],
                       idempotency_key=r["idempotency_key"]) for r in rows]

    def find_action_by_idempotency(self, key: str) -> Action | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM actions WHERE idempotency_key=? LIMIT 1", (key,)).fetchone()
        if row is None:
            return None
        return Action(id=row["id"], conversation_id=row["conversation_id"], type=row["type"],
                      payload=json.loads(row["payload"]), status=row["status"], created_at=row["created_at"],
                      idempotency_key=row["idempotency_key"])

    def update_action_status(self, action_id: str, status: str) -> Action | None:
        with self._lock, self._connection:
            self._connection.execute("UPDATE actions SET status=? WHERE id=?", (status, action_id))
            row = self._connection.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if row is None:
            return None
        return Action(id=row["id"], conversation_id=row["conversation_id"], type=row["type"],
                      payload=json.loads(row["payload"]), status=row["status"], created_at=row["created_at"],
                      idempotency_key=row["idempotency_key"])

    def add_event(self, event: Event) -> Event:
        self.ensure_conversation(event.conversation_id)
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                (event.id, event.conversation_id, event.type, event.state.value,
                 json.dumps(event.payload), event.created_at),
            )
        return event

    def list_events(self, conversation_id: str | None = None, after: str | None = None, limit: int = 100) -> list[Event]:
        query = "SELECT * FROM events"
        args: list[Any] = []
        clauses: list[str] = []
        if conversation_id:
            clauses.append("conversation_id=?")
            args.append(conversation_id)
        if after:
            clauses.append("created_at > ?")
            args.append(after)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at ASC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._connection.execute(query, args).fetchall()
        return [Event(id=r["id"], conversation_id=r["conversation_id"], type=r["type"],
                      state=r["state"], payload=json.loads(r["payload"]), created_at=r["created_at"]) for r in rows]

    def add_connection(self, connection: Connection) -> Connection:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO connections VALUES (?, ?, ?, ?, ?, ?)",
                (connection.id, connection.name, connection.kind, json.dumps(connection.config),
                 connection.status, connection.created_at),
            )
        return connection

    def list_connections(self) -> list[Connection]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM connections ORDER BY created_at DESC").fetchall()
        return [Connection(id=r["id"], name=r["name"], kind=r["kind"], config=json.loads(r["config"]),
                           status=r["status"], created_at=r["created_at"]) for r in rows]

    def update_connection_status(self, connection_id: str, status: str) -> Connection | None:
        with self._lock, self._connection:
            self._connection.execute("UPDATE connections SET status=? WHERE id=?", (status, connection_id))
            row = self._connection.execute("SELECT * FROM connections WHERE id=?", (connection_id,)).fetchone()
        if row is None:
            return None
        return Connection(id=row["id"], name=row["name"], kind=row["kind"], config=json.loads(row["config"]),
                          status=row["status"], created_at=row["created_at"])

    def update_connection(self, connection_id: str, *, name: str, kind: str,
                          config: dict[str, Any], status: str) -> Connection | None:
        with self._lock, self._connection:
            self._connection.execute("UPDATE connections SET name=?, kind=?, config=?, status=? WHERE id=?",
                                     (name, kind, json.dumps(config), status, connection_id))
            row = self._connection.execute("SELECT * FROM connections WHERE id=?", (connection_id,)).fetchone()
        if row is None:
            return None
        return Connection(id=row["id"], name=row["name"], kind=row["kind"], config=json.loads(row["config"]),
                          status=row["status"], created_at=row["created_at"])

