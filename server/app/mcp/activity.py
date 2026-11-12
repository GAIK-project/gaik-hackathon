"""The MCP activity log: one record per tool call, the last 200 kept, each change on the bus.

    record = activity.start("classify", "claude-code")           # event `call`, status running
    activity.finish(record, ok=True, ms=412, summary="classify: 3 labels, 61 characters")
    activity.recent()                                             # newest first

A record is `{id, at, tool, client, ok, ms, summary, status}`. `summary` holds the tool name
and counts only, never argument text (section 15.10). Only finished calls are stored; a
running call exists on the stream alone.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.db import get_store
from app.core.events import get_bus
from app.core.util import new_id, now_iso

logger = logging.getLogger(__name__)

TOPIC = "mcp"
EVENT = "call"
COLLECTION = "mcp_activity"
KEEP = 200
_CLIENT_MAX_CHARS = 60


def clean_client(name: str | None) -> str | None:
    """A client name fit to show: printable, one line, at most 60 characters."""
    if not name:
        return None
    text = "".join(ch for ch in str(name) if ch.isprintable()).strip()
    return text[:_CLIENT_MAX_CHARS] or None


def start(tool: str, client: str | None) -> dict[str, Any]:
    """Announce a tool call that has begun. Returns the record to pass to `finish`."""
    record: dict[str, Any] = {
        "id": new_id("mcp"),
        "at": now_iso(),
        "tool": tool,
        "client": clean_client(client),
        "ok": None,
        "ms": None,
        "summary": f"{tool}: running",
        "status": "running",
    }
    get_bus().publish(TOPIC, EVENT, dict(record))
    return record


def finish(record: dict[str, Any], *, ok: bool, ms: int, summary: str) -> dict[str, Any]:
    """Store the finished call, drop records beyond the newest 200 and announce it."""
    done = dict(record, ok=ok, ms=ms, summary=summary, status="done" if ok else "error")
    try:
        store = get_store()
        store.put(COLLECTION, done["id"], done)
        for old in store.list(COLLECTION, offset=KEEP):
            store.delete(COLLECTION, old.id)
    except Exception:
        logger.exception("Could not store MCP activity")
    get_bus().publish(TOPIC, EVENT, done)
    return done


def recent(limit: int = KEEP) -> list[dict[str, Any]]:
    """The newest finished calls, newest first."""
    limit = max(1, min(limit, KEEP))
    return [document.data for document in get_store().list(COLLECTION, limit=limit)]
