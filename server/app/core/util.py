"""Small helpers every feature needs: ids and timestamps in the shape of section 4."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime


def now_iso() -> str:
    """Current time as ISO 8601 UTC with `Z` and millisecond precision."""
    return to_iso(datetime.now(UTC))


def to_iso(moment: datetime) -> str:
    """A datetime as ISO 8601 UTC with `Z`. A naive datetime is taken as UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    """An opaque id such as `rep_3fa9c2d41b7e`."""
    return f"{prefix}_{secrets.token_hex(6)}"
