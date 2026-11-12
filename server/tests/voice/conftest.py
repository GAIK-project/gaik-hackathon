"""Fixtures for the voice tests: the app with a fake reports service and a fake Realtime."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI

from app.voice import router as voice_router
from tests.voice.fakes import FakeRealtime, FakeReports

WS_PATH = "/api/call/ws"
START = {"type": "start", "type_id": "turvallisuushavainto", "language": "en"}


@pytest.fixture
def reports(app: FastAPI) -> FakeReports:
    fake = FakeReports()
    app.dependency_overrides[voice_router.reports_service] = lambda: fake
    return fake


@pytest.fixture
def realtime(app: FastAPI, reports: FakeReports) -> Callable[..., FakeRealtime]:
    """`realtime(script)` installs a scripted Realtime server for the next call."""

    def install(script: list[list[dict]] | None = None, **options: Any) -> FakeRealtime:
        fake = FakeRealtime(script, **options)
        app.dependency_overrides[voice_router.realtime_connect] = lambda: fake.connect
        return fake

    return install


def read(ws: Any) -> dict[str, Any]:
    """The next message as `{"type": ...}`; audio is `{"type": "audio", "bytes": ...}`."""
    import json

    message = ws.receive()
    if message["type"] == "websocket.close":
        return {"type": "close", "code": message.get("code")}
    if message.get("bytes") is not None:
        return {"type": "audio", "bytes": message["bytes"]}
    return json.loads(message["text"])


def read_until(ws: Any, kind: str, **match: Any) -> list[dict[str, Any]]:
    """Read until a message of `kind` (and with the given values); return everything read."""
    seen: list[dict[str, Any]] = []
    for _ in range(400):
        message = read(ws)
        seen.append(message)
        if message["type"] == kind and all(message.get(k) == v for k, v in match.items()):
            return seen
        if message["type"] == "close":
            break
    raise AssertionError(f"no {kind} {match} in {[m['type'] for m in seen]}")


def wait_for(condition: Callable[[], bool], seconds: float = 3.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")
