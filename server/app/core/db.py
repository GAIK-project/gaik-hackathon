"""SQLite at `$DATA_DIR/app.db` (WAL) and the small document store features share.

A document is a JSON object stored by collection and id, with `created_at` and `updated_at`.
Every method is synchronous, takes well under a millisecond for the sizes of this app and may
be called from the event loop or from a worker thread: one connection, guarded by a lock.

    store = get_store()
    store.put("reports", report.id, report)          # a dict or a pydantic model
    doc = store.get("reports", report_id)            # Document | None; doc.data is the dict
    store.update("reports", report_id, lambda data: {**data, "status": "sent"})
    store.list("reports", limit=50)                  # newest first
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from app.core.settings import get_settings
from app.core.util import now_iso

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    collection TEXT NOT NULL,
    id         TEXT NOT NULL,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (collection, id)
);
CREATE INDEX IF NOT EXISTS documents_created ON documents (collection, created_at);
CREATE INDEX IF NOT EXISTS documents_updated ON documents (collection, updated_at);
"""

OrderBy = Literal["created_at", "updated_at"]


@dataclass(frozen=True, slots=True)
class Document:
    collection: str
    id: str
    data: dict[str, Any]
    created_at: str
    updated_at: str


def _to_json(data: dict[str, Any] | BaseModel) -> str:
    if isinstance(data, BaseModel):
        return data.model_dump_json()
    if not isinstance(data, dict):
        raise TypeError("a document is a dict or a pydantic model")
    return json.dumps(data, ensure_ascii=False)


class DocumentStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA)

    # documents -----------------------------------------------------------------------------

    def put(self, collection: str, id: str, data: dict[str, Any] | BaseModel) -> Document:
        """Create or replace a document. `created_at` is kept when the document exists."""
        payload = _to_json(data)
        now = now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO documents (collection, id, data, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (collection, id) DO UPDATE SET "
                "data = excluded.data, updated_at = excluded.updated_at",
                (collection, id, payload, now, now),
            )
            row = self._row(conn, collection, id)
        assert row is not None
        return row

    def get(self, collection: str, id: str) -> Document | None:
        with self._lock:
            return self._row(self._conn, collection, id)

    def update(
        self, collection: str, id: str, change: Callable[[dict[str, Any]], dict[str, Any] | None]
    ) -> Document | None:
        """Read, change and write one document as one step, so two threads cannot lose an edit.

        `change` gets the stored dict and returns the new one (or edits it in place and returns
        nothing). Returns the stored document, or `None` when there is no such document.
        """
        with self.transaction() as conn:
            current = self._row(conn, collection, id)
            if current is None:
                return None
            changed = change(current.data)
            payload = _to_json(current.data if changed is None else changed)
            conn.execute(
                "UPDATE documents SET data = ?, updated_at = ? WHERE collection = ? AND id = ?",
                (payload, now_iso(), collection, id),
            )
            return self._row(conn, collection, id)

    def list(
        self,
        collection: str,
        *,
        limit: int | None = None,
        offset: int = 0,
        order_by: OrderBy = "created_at",
        newest_first: bool = True,
    ) -> list[Document]:
        if order_by not in ("created_at", "updated_at"):
            raise ValueError("order_by is created_at or updated_at")
        direction = "DESC" if newest_first else "ASC"
        sql = (
            "SELECT collection, id, data, created_at, updated_at FROM documents "
            f"WHERE collection = ? ORDER BY {order_by} {direction}, rowid {direction} "
            "LIMIT ? OFFSET ?"
        )
        with self._lock:
            rows = self._conn.execute(
                sql, (collection, -1 if limit is None else max(0, limit), max(0, offset))
            ).fetchall()
        return [self._document(row) for row in rows]

    def delete(self, collection: str, id: str) -> bool:
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM documents WHERE collection = ? AND id = ?", (collection, id)
            )
            return cursor.rowcount > 0

    def count(self, collection: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM documents WHERE collection = ?", (collection,)
            ).fetchone()
        return int(row[0])

    # plumbing ------------------------------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """The connection inside one write transaction, for a feature that keeps its own table.

        Do not keep the connection, and do not call a model inside the block: every other
        thread waits for it.
        """
        with self._lock:
            nested = self._conn.in_transaction
            if not nested:
                self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                if not nested:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                if not nested:
                    self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _row(self, conn: sqlite3.Connection, collection: str, id: str) -> Document | None:
        row = conn.execute(
            "SELECT collection, id, data, created_at, updated_at FROM documents "
            "WHERE collection = ? AND id = ?",
            (collection, id),
        ).fetchone()
        return self._document(row) if row is not None else None

    @staticmethod
    def _document(row: sqlite3.Row) -> Document:
        return Document(
            collection=row["collection"],
            id=row["id"],
            data=json.loads(row["data"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


_store: DocumentStore | None = None
_store_lock = threading.Lock()


def get_store() -> DocumentStore:
    """The process-wide store at `$DATA_DIR/app.db`, opened on first use."""
    global _store
    with _store_lock:
        if _store is None:
            _store = DocumentStore(get_settings().data_path / "app.db")
        return _store


def close_store() -> None:
    """Close the process-wide store (shutdown, and tests that switch `DATA_DIR`)."""
    global _store
    with _store_lock:
        if _store is not None:
            _store.close()
            _store = None
