"""The bridge session and the queue of raw webhook deliveries, in process memory only.

The API never reads what Meta sends. While a bridge session is active it keeps each delivery
as the exact bytes received plus the `X-Hub-Signature-256` header, and hands them to the
bridge on the owner's machine, which holds the keys and does the work. Nothing here is
written to disk or logged, and everything is dropped when the session ends or expires
(12 hours without a poll).

    hub = get_hub()
    hub.start(verify_token)                  # one session at a time; a new one replaces the old
    hub.challenge_ok(mode, token)            # Meta's handshake
    hub.store(body, signature_header)        # False when dropped
    items = await hub.take(wait_s=25)        # long poll; raises NoSession without a session
    hub.end()

Bounds: 200 deliveries of at most 256 KB each; when full, the oldest is dropped.
"""

from __future__ import annotations

import base64
import hmac
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import anyio

from app.core.util import now_iso

MAX_ITEMS = 200
MAX_ITEM_BYTES = 256 * 1024
SESSION_TTL_S = 12 * 60 * 60
MAX_WAIT_S = 30.0
POLL_STEP_S = 0.2

_SIGNATURE = re.compile(r"sha256=[0-9a-f]{64}")


class NoSession(Exception):
    """There is no active bridge session."""


@dataclass(frozen=True, slots=True)
class Delivery:
    id: str
    received_at: str
    signature: str
    body: bytes

    def as_json(self) -> dict[str, str]:
        return {
            "id": self.id,
            "received_at": self.received_at,
            "signature": self.signature,
            "body_b64": base64.b64encode(self.body).decode("ascii"),
        }


class BridgeHub:
    """One bridge session and its bounded queue. Safe to call from any thread."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._items: deque[Delivery] = deque(maxlen=MAX_ITEMS)
        self._verify_token: str | None = None
        self._generation = 0
        self._last_seen = 0.0
        self._last_poll_at: str | None = None
        self._closing = False

    # session ----------------------------------------------------------------------------

    def start(self, verify_token: str) -> None:
        """Start a session. An earlier session and whatever it had queued are dropped."""
        with self._lock:
            self._items.clear()
            self._verify_token = verify_token
            self._generation += 1
            self._last_seen = self._clock()
            self._last_poll_at = None

    def end(self) -> None:
        with self._lock:
            self._drop()

    def _drop(self) -> None:
        self._items.clear()
        self._verify_token = None
        self._generation += 1
        self._last_poll_at = None

    def _active(self) -> bool:
        """Whether a session is live. Call with the lock held: an expired one is dropped."""
        if self._verify_token is None:
            return False
        if self._clock() - self._last_seen > SESSION_TTL_S:
            self._drop()
            return False
        return True

    def active(self) -> bool:
        with self._lock:
            return self._active()

    def challenge_ok(self, mode: str | None, token: str | None) -> bool:
        """Meta's handshake: `hub.mode=subscribe` and the verify token of the live session."""
        with self._lock:
            if not self._active() or mode != "subscribe" or not token:
                return False
            return hmac.compare_digest(token.encode(), (self._verify_token or "").encode())

    # queue ------------------------------------------------------------------------------

    def store(self, body: bytes, signature: str | None) -> bool:
        """Keep one delivery. Dropped without a session, without a well-formed signature
        header, or when the body is empty or over 256 KB."""
        if not signature or _SIGNATURE.fullmatch(signature) is None:
            return False
        if not body or len(body) > MAX_ITEM_BYTES:
            return False
        with self._lock:
            if not self._active():
                return False
            self._items.append(
                Delivery(
                    id=f"wh_{secrets.token_hex(8)}",
                    received_at=now_iso(),
                    signature=signature,
                    body=bytes(body),
                )
            )
            return True

    def _take_now(self, generation: int | None) -> tuple[list[Delivery], int]:
        with self._lock:
            if not self._active() or (generation is not None and generation != self._generation):
                raise NoSession
            self._last_seen = self._clock()
            self._last_poll_at = now_iso()
            items = list(self._items)
            self._items.clear()
            return items, self._generation

    async def take(self, wait_s: float = 25.0) -> list[Delivery]:
        """Return what is queued and remove it, waiting up to `wait_s` for a first delivery.
        Raises `NoSession` when there is no session, or when it ends during the wait."""
        deadline = self._clock() + max(0.0, min(wait_s, MAX_WAIT_S))
        items, generation = self._take_now(None)
        while not items and not self._closing and self._clock() < deadline:
            await anyio.sleep(POLL_STEP_S)
            items, generation = self._take_now(generation)
        return items

    def status(self) -> dict[str, object]:
        with self._lock:
            active = self._active()
            return {
                "active": active,
                "last_poll_at": self._last_poll_at if active else None,
                "queued": len(self._items),
            }

    # app lifetime -----------------------------------------------------------------------

    def open(self) -> None:
        self._closing = False

    def close(self) -> None:
        """Server shutdown: waiting polls return at once and the session is dropped."""
        self._closing = True
        self.end()


_hub = BridgeHub()


def get_hub() -> BridgeHub:
    return _hub
