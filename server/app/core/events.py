"""In-process event bus and server-sent events (docs/ARCHITECTURE.md section 4).

    bus = get_bus()
    bus.publish("reports", "report", summary)        # from the event loop or a worker thread

    @router.get("/api/reports/stream")
    async def stream() -> StreamingResponse:
        return sse_response(bus.subscribe("reports"))     # GET stream for EventSource

    @router.post("/api/studio/generate")
    async def generate(body: Body) -> StreamingResponse:
        async def steps():
            yield "status", {"text": "Luen kuvausta"}
            yield "spec", spec
        return sse_response(steps())                       # POST stream for fetch

Wire format: `event: <name>\\ndata: <json>\\n\\n`, a `: ping` comment every 15 s of silence and a
final `event: done`. An exception inside the source becomes one `event: error` with the
`{"code", "message"}` of the error envelope, then `done`.

One process, one replica: the bus does not cross processes. A subscriber whose queue is full
is dropped (its stream ends, `EventSource` reconnects and reads the current state again).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Any

from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.errors import GENERIC_MESSAGE, AppError

logger = logging.getLogger(__name__)

PING_SECONDS = 15.0
QUEUE_SIZE = 256
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

_CLOSED = object()


@dataclass(frozen=True, slots=True)
class Event:
    name: str
    data: Any = None


class Subscription:
    """An async iterator of `Event`s for one topic. Use it once, from the loop that made it."""

    def __init__(self, bus: EventBus, topic: str, maxsize: int) -> None:
        self.topic = topic
        self.dropped = False
        self._bus = bus
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> Event:
        if self._closed and self._queue.empty():
            raise StopAsyncIteration
        item = await self._queue.get()
        if item is _CLOSED:
            raise StopAsyncIteration
        return item

    async def __aenter__(self) -> Subscription:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.close()

    async def aclose(self) -> None:
        self.close()

    def close(self) -> None:
        """Stop receiving. Events already queued are still delivered, then iteration ends."""
        self._bus._remove(self)
        self._finish(discard=False)

    # called on the subscriber's loop only ---------------------------------------------------

    def _offer(self, event: Event) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped = True
            self._bus._remove(self)
            self._finish(discard=True)
            logger.warning("Dropped a slow subscriber of topic %s", self.topic)

    def _end(self) -> None:
        self._finish(discard=False)

    def _finish(self, *, discard: bool) -> None:
        if self._closed:
            return
        self._closed = True
        if discard:
            while not self._queue.empty():
                self._queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(_CLOSED)  # wakes a waiting reader; a full queue needs none


class EventBus:
    def __init__(self, maxsize: int = QUEUE_SIZE) -> None:
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._topics: dict[str, set[Subscription]] = {}

    def subscribe(self, topic: str, *, maxsize: int | None = None) -> Subscription:
        """Start receiving the events of a topic. Call from a coroutine."""
        subscription = Subscription(self, topic, maxsize or self._maxsize)
        with self._lock:
            self._topics.setdefault(topic, set()).add(subscription)
        return subscription

    def publish(self, topic: str, name: str, data: Any = None) -> int:
        """Send an event to the subscribers of a topic; returns how many there were.

        Never blocks and never raises for a slow or gone subscriber. Safe from worker threads.
        """
        with self._lock:
            subscribers = list(self._topics.get(topic, ()))
        if not subscribers:
            return 0
        event = Event(name, data)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        for subscription in subscribers:
            if subscription._loop is running:
                subscription._offer(event)
                continue
            try:
                subscription._loop.call_soon_threadsafe(subscription._offer, event)
            except RuntimeError:  # the subscriber's loop is closed
                self._remove(subscription)
        return len(subscribers)

    def subscriber_count(self, topic: str) -> int:
        with self._lock:
            return len(self._topics.get(topic, ()))

    def close(self) -> None:
        """End every subscription (shutdown): open streams finish with `done`."""
        with self._lock:
            subscriptions = [item for group in self._topics.values() for item in group]
            self._topics.clear()
        for subscription in subscriptions:
            try:
                subscription._loop.call_soon_threadsafe(subscription._end)
            except RuntimeError:  # the subscriber's loop is closed
                subscription._closed = True

    def _remove(self, subscription: Subscription) -> None:
        with self._lock:
            group = self._topics.get(subscription.topic)
            if group is None:
                return
            group.discard(subscription)
            if not group:
                del self._topics[subscription.topic]


_bus = EventBus()


def get_bus() -> EventBus:
    """The process-wide bus."""
    return _bus


# server-sent events ------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def format_sse(name: str, data: Any = None) -> bytes:
    """One event on the wire. `data` is a pydantic model or anything `json.dumps` takes."""
    safe_name = "".join(name.split()) or "message"
    payload = json.dumps({} if data is None else data, ensure_ascii=False, default=_jsonable)
    return f"event: {safe_name}\ndata: {payload}\n\n".encode()


PING_FRAME = b": ping\n\n"


def _pair(item: Any) -> tuple[str, Any]:
    if isinstance(item, Event):
        return item.name, item.data
    name, data = item
    return name, data


async def sse_frames(
    source: AsyncIterable[Event | tuple[str, Any]],
    *,
    initial: Iterable[Event | tuple[str, Any]] = (),
    ping_seconds: float = PING_SECONDS,
) -> AsyncIterator[bytes]:
    """The byte frames of an SSE stream: `initial`, then `source`, pings while idle, `done`."""
    iterator = source.__aiter__()
    pending: asyncio.Future[Any] | None = None
    try:
        for item in initial:
            yield format_sse(*_pair(item))
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            finished, _ = await asyncio.wait({pending}, timeout=ping_seconds)
            if not finished:
                yield PING_FRAME
                continue
            task, pending = pending, None
            try:
                item = task.result()
            except StopAsyncIteration:
                break
            except AppError as error:
                yield format_sse("error", {"code": error.code, "message": error.message})
                break
            except Exception as error:  # noqa: BLE001  (provider text must not reach the client)
                logger.error("SSE source failed with %s", type(error).__name__, exc_info=error)
                yield format_sse("error", {"code": "internal_error", "message": GENERIC_MESSAGE})
                break
            yield format_sse(*_pair(item))
        yield format_sse("done")
    finally:
        subscription_close = getattr(iterator, "close", None)
        if callable(subscription_close):
            subscription_close()  # a bus subscription: synchronous, cannot be interrupted
        # A disconnect cancels this task, and an awaited clean-up would be cancelled with it:
        # run it as its own task and wait for it only as long as we are allowed to.
        cleanup = asyncio.ensure_future(_close_source(pending, iterator))
        _cleanups.add(cleanup)
        cleanup.add_done_callback(_cleanups.discard)
        await asyncio.shield(cleanup)


_cleanups: set[asyncio.Future[None]] = set()


async def _close_source(pending: asyncio.Future[Any] | None, iterator: Any) -> None:
    if pending is not None:
        if not pending.done():
            pending.cancel()
        with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
            await pending
    close = getattr(iterator, "aclose", None)
    if close is not None:
        with contextlib.suppress(Exception):
            await close()


def sse_response(
    source: AsyncIterable[Event | tuple[str, Any]],
    *,
    initial: Iterable[Event | tuple[str, Any]] = (),
    ping_seconds: float = PING_SECONDS,
) -> StreamingResponse:
    """Stream `(name, data)` pairs or bus events as server-sent events.

    `source` is a bus `Subscription` (GET streams) or an async generator (POST streams).
    `initial` is sent first, for example the current state before the live changes.
    """
    return StreamingResponse(
        sse_frames(source, initial=initial, ping_seconds=ping_seconds),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


def sse_stream(
    generator: AsyncIterable[tuple[str, Any]], *, ping_seconds: float = PING_SECONDS
) -> StreamingResponse:
    """A POST stream from an async generator of `(name, data)` pairs. Same as `sse_response`."""
    return sse_response(generator, ping_seconds=ping_seconds)
