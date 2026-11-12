from __future__ import annotations

import asyncio
import json
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import AppError
from app.core.events import (
    PING_FRAME,
    Event,
    EventBus,
    format_sse,
    get_bus,
    sse_frames,
    sse_response,
    sse_stream,
)


async def take(subscription, count: int, wait: float = 2.0) -> list[Event]:
    return [await asyncio.wait_for(anext(subscription), wait) for _ in range(count)]


async def collect(frames) -> list[bytes]:
    return [frame async for frame in frames]


def parse(frame: bytes) -> tuple[str, object]:
    lines = frame.decode().split("\n")
    assert lines[0].startswith("event: ") and lines[1].startswith("data: ")
    assert lines[2:] == ["", ""]
    return lines[0][7:], json.loads(lines[1][6:])


# bus ---------------------------------------------------------------------------------------


async def test_subscriber_gets_events_of_its_topic_in_order():
    bus = EventBus()
    reports = bus.subscribe("reports")
    other = bus.subscribe("mcp")

    assert bus.publish("reports", "report", {"id": "rep_1"}) == 1
    assert bus.publish("reports", "report", {"id": "rep_2"}) == 1
    assert bus.publish("nobody", "report", {}) == 0

    events = await take(reports, 2)
    assert events == [Event("report", {"id": "rep_1"}), Event("report", {"id": "rep_2"})]
    bus.publish("mcp", "call", {"tool": "classify"})
    assert (await take(other, 1))[0].name == "call"


async def test_every_subscriber_of_a_topic_gets_the_event():
    bus = EventBus()
    first, second = bus.subscribe("reports"), bus.subscribe("reports")
    assert bus.publish("reports", "report", 1) == 2
    assert (await take(first, 1))[0].data == 1
    assert (await take(second, 1))[0].data == 1


async def test_publish_from_a_worker_thread():
    bus = EventBus()
    subscription = bus.subscribe("reports")
    thread = threading.Thread(target=lambda: bus.publish("reports", "report", {"from": "thread"}))
    thread.start()
    thread.join()
    assert (await take(subscription, 1))[0].data == {"from": "thread"}


async def test_closed_subscription_delivers_what_it_has_and_ends():
    bus = EventBus()
    subscription = bus.subscribe("reports")
    bus.publish("reports", "report", 1)
    subscription.close()
    assert bus.subscriber_count("reports") == 0
    assert bus.publish("reports", "report", 2) == 0
    assert [event.data async for event in subscription] == [1]


async def test_context_manager_unsubscribes():
    bus = EventBus()
    async with bus.subscribe("reports") as subscription:
        assert bus.subscriber_count("reports") == 1
        assert subscription.topic == "reports"
    assert bus.subscriber_count("reports") == 0


async def test_slow_subscriber_is_dropped_and_the_others_keep_going():
    bus = EventBus()
    slow = bus.subscribe("reports", maxsize=2)
    healthy = bus.subscribe("reports", maxsize=10)

    for number in range(3):
        bus.publish("reports", "report", number)

    assert slow.dropped is True
    assert bus.subscriber_count("reports") == 1
    assert [event async for event in slow] == []  # ends at once: the client reconnects
    assert bus.publish("reports", "report", 3) == 1
    assert [event.data for event in await take(healthy, 4)] == [0, 1, 2, 3]
    assert healthy.dropped is False


async def test_publish_never_blocks_or_raises_with_a_full_queue():
    bus = EventBus(maxsize=1)
    bus.subscribe("reports")
    for number in range(100):
        bus.publish("reports", "report", number)
    assert bus.subscriber_count("reports") == 0


async def test_closing_the_bus_ends_open_streams_with_done():
    bus = EventBus()
    subscription = bus.subscribe("reports")
    bus.publish("reports", "report", {"id": "rep_1"})
    bus.close()
    frames = await asyncio.wait_for(collect(sse_frames(subscription)), 2)
    assert [parse(frame)[0] for frame in frames] == ["report", "done"]


# SSE framing -------------------------------------------------------------------------------


def test_format_is_event_line_data_line_blank_line():
    assert format_sse("report", {"id": "rep_1", "title": "Työmaa"}) == (
        'event: report\ndata: {"id": "rep_1", "title": "Työmaa"}\n\n'.encode()
    )
    assert format_sse("done") == b"event: done\ndata: {}\n\n"


def test_data_is_one_line_and_models_are_dumped():
    class Step(BaseModel):
        id: str
        note: str | None = None

    frame = format_sse("step", {"step": Step(id="s1"), "text": "rivi 1\nrivi 2"})
    assert frame.count(b"\n") == 3
    name, data = parse(frame)
    assert name == "step"
    assert data == {"step": {"id": "s1", "note": None}, "text": "rivi 1\nrivi 2"}
    assert parse(format_sse("step", Step(id="s2", note="ok")))[1] == {"id": "s2", "note": "ok"}
    assert parse(format_sse("bad name\nx", 1))[0] == "badnamex"


async def test_generator_stream_ends_with_done():
    async def steps():
        yield "status", {"text": "Luen kuvausta"}
        yield "spec", {"id": "spec_1"}

    frames = await collect(sse_frames(steps()))
    assert [parse(frame) for frame in frames] == [
        ("status", {"text": "Luen kuvausta"}),
        ("spec", {"id": "spec_1"}),
        ("done", {}),
    ]


async def test_initial_events_come_first():
    async def steps():
        yield "report", {"id": "rep_2"}

    frames = await collect(sse_frames(steps(), initial=[("report", {"id": "rep_1"})]))
    assert [parse(frame)[1] for frame in frames] == [{"id": "rep_1"}, {"id": "rep_2"}, {}]


async def test_ping_comment_while_the_source_is_quiet():
    async def slow():
        await asyncio.sleep(0.12)
        yield "result", {"ok": True}

    frames = await collect(sse_frames(slow(), ping_seconds=0.03))
    assert frames[0] == PING_FRAME == b": ping\n\n"
    assert frames.count(PING_FRAME) >= 2
    assert [parse(frame)[0] for frame in frames if frame != PING_FRAME] == ["result", "done"]


async def test_app_error_becomes_an_error_event_then_done():
    async def failing():
        yield "status", {"text": "Aloitan"}
        raise AppError("spec_invalid", "Kuvauksesta ei saatu toimivaa sovellusta.", 422)

    frames = await collect(sse_frames(failing()))
    assert [parse(frame) for frame in frames] == [
        ("status", {"text": "Aloitan"}),
        ("error", {"code": "spec_invalid", "message": "Kuvauksesta ei saatu toimivaa sovellusta."}),
        ("done", {}),
    ]


async def test_unexpected_error_does_not_leak_its_text():
    async def failing():
        raise RuntimeError("provider said: key sk-secret is invalid")
        yield  # pragma: no cover

    frames = await collect(sse_frames(failing()))
    assert [parse(frame)[0] for frame in frames] == ["error", "done"]
    assert parse(frames[0])[1]["code"] == "internal_error"
    assert b"sk-secret" not in b"".join(frames)


async def test_abandoned_stream_unsubscribes_and_closes_its_source():
    bus = EventBus()
    subscription = bus.subscribe("reports")
    frames = sse_frames(subscription, ping_seconds=0.02)
    bus.publish("reports", "report", {"id": "rep_1"})
    assert parse(await anext(frames))[0] == "report"
    assert await anext(frames) == PING_FRAME
    await frames.aclose()  # what a client disconnect does
    assert bus.subscriber_count("reports") == 0

    closed = asyncio.Event()

    async def source():
        try:
            yield "status", {}
            await asyncio.sleep(60)
        finally:
            closed.set()

    frames = sse_frames(source(), ping_seconds=0.02)
    await anext(frames)
    await anext(frames)  # a ping: the source is now waiting inside its sleep
    await frames.aclose()
    await asyncio.wait_for(closed.wait(), 2)


def test_response_headers_and_body_over_http():
    app = FastAPI()

    @app.post("/stream")
    async def stream():
        async def steps():
            yield "step", {"id": "parse", "status": "running"}
            yield "result", {"kind": "table"}

        return sse_stream(steps())

    @app.get("/live")
    async def live():
        bus = get_bus()
        subscription = bus.subscribe("test-live")
        bus.publish("test-live", "report", {"id": "rep_1"})
        subscription.close()  # ends after what is queued
        return sse_response(subscription, initial=[("report", {"id": "rep_0"})])

    client = TestClient(app)
    response = client.post("/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.text == (
        'event: step\ndata: {"id": "parse", "status": "running"}\n\n'
        'event: result\ndata: {"kind": "table"}\n\n'
        "event: done\ndata: {}\n\n"
    )

    live_text = client.get("/live").text
    assert live_text == (
        'event: report\ndata: {"id": "rep_0"}\n\n'
        'event: report\ndata: {"id": "rep_1"}\n\n'
        "event: done\ndata: {}\n\n"
    )
