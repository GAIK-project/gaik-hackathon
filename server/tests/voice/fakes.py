"""Fakes for the voice tests: an in-memory `reports.service` and a scripted Realtime server."""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from typing import Any

from app.core.models import FieldDef, FieldValue, Report, ReportType, TimelineEvent

SAFETY = ReportType(
    id="turvallisuushavainto",
    title="Safety observation",
    description="A hazard or near miss seen at work.",
    icon="shield-alert",
    requirement="Record what was seen, where and how serious it is.",
    builtin=True,
    fields=[
        FieldDef(
            name="description",
            label="What happened",
            type="longtext",
            required=True,
            description="What was observed.",
        ),
        FieldDef(
            name="location",
            label="Location",
            type="text",
            required=True,
            description="Where it was seen.",
        ),
        FieldDef(
            name="severity",
            label="Severity",
            type="choice",
            required=True,
            description="How serious it is.",
            options=["low", "medium", "high"],
        ),
        FieldDef(
            name="action",
            label="Action taken",
            type="text",
            required=False,
            description="What was done on the spot.",
        ),
    ],
)

NOW = "2026-11-12T10:00:00Z"


class FakeReports:
    """The async functions of `app.reports.service` the relay uses, kept in memory."""

    def __init__(self, types: list[ReportType] | None = None) -> None:
        self.types = {item.id: item for item in (types or [SAFETY])}
        self.reports: dict[str, dict[str, Any]] = {}
        self.finished: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.lock = threading.Lock()

    async def get_report_type(self, type_id: str) -> ReportType | None:
        return self.types.get(type_id)

    async def create_report(
        self, type_id: str, channel: str, caller_label: str, status: str = "processing"
    ) -> Report:
        with self.lock:
            report_id = f"rep_{len(self.reports) + 1:04d}"
            self.reports[report_id] = {
                "type_id": type_id,
                "channel": channel,
                "caller_label": caller_label,
                "status": status,
                "values": {},
                "transcript": [],
                "events": [],
            }
        return self._report(report_id)

    async def get_report(self, report_id: str) -> Report | None:
        return self._report(report_id) if report_id in self.reports else None

    async def set_fields(
        self, report_id: str, values: dict[str, str | None], source: str
    ) -> Report:
        self.reports[report_id]["values"].update(values)
        return self._report(report_id)

    async def add_transcript(self, report_id: str, role: str, text: str) -> None:
        self.reports[report_id]["transcript"].append((role, text))

    async def add_event(
        self,
        report_id: str,
        kind: str,
        label: str,
        *,
        detail: str | None = None,
        component: str | None = None,
        status: str = "done",
        ms: int | None = None,
    ) -> TimelineEvent:
        events = self.reports[report_id]["events"]
        event = TimelineEvent(
            id=f"evt_{len(events) + 1}",
            at=NOW,
            kind=kind,  # type: ignore[arg-type]
            label=label,
            detail=detail,
            component=component,
            status=status,  # type: ignore[arg-type]
            ms=ms,
        )
        events.append(event)
        return event

    async def finish_call(self, report_id: str) -> None:
        self.finished.append(report_id)
        self.reports[report_id]["status"] = "processing"

    async def fail_report(self, report_id: str, detail: str) -> None:
        self.failed.append((report_id, detail))
        self.reports[report_id]["status"] = "failed"

    def _report(self, report_id: str) -> Report:
        state = self.reports[report_id]
        report_type = self.types[state["type_id"]]
        values: dict[str, str | None] = state["values"]
        required = [field for field in report_type.fields if field.required]
        return Report(
            id=report_id,
            created_at=NOW,
            updated_at=NOW,
            channel=state["channel"],
            caller_label=state["caller_label"],
            type_id=report_type.id,
            type_title=report_type.title,
            status=state["status"],
            filled=sum(1 for field in required if values.get(field.name)),
            total=len(required),
            urgency=None,
            headline="",
            fields=[
                FieldValue(
                    name=field.name,
                    label=field.label,
                    value=values.get(field.name),
                    evidence=None,
                    source="voice",
                )
                for field in report_type.fields
            ],
            missing=[field.label for field in required if not values.get(field.name)],
            transcript=[],
            timeline=list(state["events"]),
            files=[],
            email=None,
        )


def speak(text: str, item_id: str = "item_a", audio: bytes = b"\x01\x00" * 480) -> list[dict]:
    """The events of a spoken response."""
    return [
        {"type": "response.created", "response": {"id": "resp"}},
        {
            "type": "response.output_audio.delta",
            "item_id": item_id,
            "delta": base64.b64encode(audio).decode(),
        },
        {"type": "response.output_audio_transcript.delta", "item_id": item_id, "delta": text[:4]},
        {"type": "response.output_audio_transcript.delta", "item_id": item_id, "delta": text[4:]},
        {"type": "response.output_audio_transcript.done", "item_id": item_id, "transcript": text},
        {"type": "response.done", "response": {"status": "completed"}},
    ]


def call_tool(name: str, arguments: dict[str, Any], call_id: str) -> list[dict]:
    """The events of a response that calls one tool and says nothing."""
    return [
        {"type": "response.created", "response": {"id": "resp"}},
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(arguments),
            },
        },
        {"type": "response.done", "response": {"status": "completed"}},
    ]


class FakeRealtime:
    """A scripted Realtime server. Each `response.create` plays the next entry of `script`.

    `sent` holds every event the relay sent; `emit` injects an event from the test thread
    (speech started, a transcript); `drop` closes the connection from the provider side.
    """

    def __init__(self, script: list[list[dict]] | None = None, *, refuse_sessions: int = 0) -> None:
        self.script = list(script or [])
        self.refuse_sessions = refuse_sessions
        self.sent: list[dict[str, Any]] = []
        self.url = ""
        self.headers: dict[str, str] = {}
        self.closed = False
        self.connected = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[dict[str, Any] | None] | None = None

    async def connect(self, url: str, headers: dict[str, str]) -> FakeRealtime:
        self.url, self.headers = url, headers
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self.connected.set()
        return self

    async def send(self, message: str) -> None:
        assert self._queue is not None
        event = json.loads(message)
        self.sent.append(event)
        kind = event["type"]
        if kind == "session.update":
            if self.refuse_sessions > 0:
                self.refuse_sessions -= 1
                self._queue.put_nowait({"type": "error", "error": {"code": "invalid_value"}})
            else:
                self._queue.put_nowait({"type": "session.updated", "session": event["session"]})
        elif kind == "response.create" and self.script:
            for item in self.script.pop(0):
                self._queue.put_nowait(item)

    async def recv(self) -> str:
        assert self._queue is not None
        event = await self._queue.get()
        if event is None:
            raise ConnectionError("closed")
        return json.dumps(event)

    async def close(self) -> None:
        self.closed = True

    def emit(self, *events: dict[str, Any] | None) -> None:
        assert self._loop is not None and self._queue is not None
        for event in events:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    def drop(self) -> None:
        self.emit(None)

    def sent_of(self, kind: str) -> list[dict[str, Any]]:
        return [event for event in self.sent if event["type"] == kind]
