"""One call: the client WebSocket on one side, the Realtime session on the other.

Protocol with the client: docs/ARCHITECTURE.md section 8.2 with 15.3 and 15.7.

    relay = CallRelay(websocket, service=reports_service, connect=open_realtime)
    await relay.run()        # returns when the call is over; never raises to the route

`service` is `app.reports.service` (or a fake with the same async functions); `connect` is an
async callable `(url, headers) -> connection` where the connection has `send(str)`, `recv()`
and `close()` (a `websockets` client connection, or a fake).

What the client can rely on:

- `transcript` messages carry the whole text of the item so far (replace by `item_id`); the
  last one for an item has `final: true`.
- `state` is `listening` only when the agent has finished and waits for the caller.
- After `save_report` the agent says goodbye, then `saved`, `state: done` and a normal close.
- A call that ends any other way (stop frame, disconnect, time limit, provider failure) keeps
  the report: with any field set the pipeline runs on the transcript so far, else it is failed.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from app.core import limits
from app.core.auth import auth_kind, key_scope
from app.core.models import ReportType
from app.core.settings import get_settings
from app.voice import session as voice_session

logger = logging.getLogger(__name__)

START_TIMEOUT_SECONDS = 15.0
SESSION_TIMEOUT_SECONDS = 20.0
GOODBYE_TIMEOUT_SECONDS = 20.0
WATCHDOG_INTERVAL_SECONDS = 1.0
MAX_TOOL_ROUNDS_PER_TURN = 4
MAX_TEXT_CHARS = 2_000
MAX_AUDIO_FRAME_BYTES = 96_000  # two seconds of PCM16 at 24 kHz; frames are 20 to 60 ms

DEFAULT_CHANNEL = "call"
DEFAULT_CALLER_LABEL = "Browser call"
BRIDGE_CHANNELS = ("call", "whatsapp")

MSG_BUSY = "All lines are busy. Try again in a moment."
MSG_MODELS_OFF = "Calls are switched off right now."
MSG_BAD_START = "The call could not start: the first message must be a start message."
MSG_UNKNOWN_TYPE = "The call could not start: unknown report type."
MSG_UNAVAILABLE = "The voice service did not answer. Try again in a moment."
MSG_LOST = "The connection to the voice service was lost."
MSG_END = {
    "duration": "The call reached its maximum length.",
    "silence": "The call ended because the line was silent.",
}
DETAIL_CUT_OFF = "The call was cut off"


class RealtimeConnection(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


Connect = Callable[[str, dict[str, str]], Awaitable[RealtimeConnection]]


async def open_realtime(url: str, headers: dict[str, str]) -> RealtimeConnection:
    """Open the Realtime WebSocket (the real `connect`)."""
    import websockets

    return await websockets.connect(
        url, additional_headers=headers, open_timeout=15, max_size=None, ping_interval=20
    )


class _CallOver(Exception):
    """Raised inside the relay to leave the loops; the reason is already recorded."""


class CallRelay:
    def __init__(self, websocket: WebSocket, *, service: Any, connect: Connect) -> None:
        self.ws = websocket
        self.service = service
        self.connect = connect
        self.realtime: RealtimeConnection | None = None
        self.report_id: str | None = None
        self.report_type: ReportType | None = None
        self.language = voice_session.DEFAULT_LANGUAGE
        self.clock: limits.CallClock | None = None

        self._values: dict[str, str] = {}
        self._saved = False
        self._goodbye_pending = False
        self._response_active = False
        self._needs_response = False
        self._tool_rounds = 0
        self._speaking = False
        self._handled_calls: set[str] = set()
        self._agent_text: dict[str, str] = {}
        self._caller_text: dict[str, str] = {}
        self._pending_caller_items: set[str] = set()
        self._held_agent_turns: list[str] = []
        self._transcription_variant = 0
        self._turns = 0
        self._outcome = "cut_off"
        self._started = time.monotonic()
        self._send_lock = asyncio.Lock()

    # ---- entry point ---------------------------------------------------------------------

    async def run(self) -> None:
        await self.ws.accept()
        if limits.models_off():
            await self._refuse(MSG_MODELS_OFF)
            return
        start = await self._read_start()
        if start is None:
            return
        if not limits.acquire_call():
            await self._refuse(MSG_BUSY)
            return
        started = time.monotonic()
        try:
            await self._call(start)
        except _CallOver:
            pass
        except WebSocketDisconnect:
            logger.info("call %s: client disconnected", self.report_id)
        except Exception:
            logger.exception("call %s failed", self.report_id)
            await self._send_json({"type": "error", "message": MSG_LOST})
        finally:
            limits.release_call()
            await self._finish(time.monotonic() - started)

    # ---- start ---------------------------------------------------------------------------

    async def _read_start(self) -> dict[str, Any] | None:
        try:
            message = await asyncio.wait_for(self.ws.receive(), START_TIMEOUT_SECONDS)
        except (TimeoutError, WebSocketDisconnect):
            await self._refuse(MSG_BAD_START)
            return None
        frame = _parse(message.get("text"))
        type_id = frame.get("type_id") if frame.get("type") == "start" else None
        if not isinstance(type_id, str) or not type_id:
            await self._refuse(MSG_BAD_START)
            return None
        return frame

    async def _call(self, start: dict[str, Any]) -> None:
        report_type = await self.service.get_report_type(start["type_id"])
        if report_type is None:
            await self._send_json({"type": "error", "message": MSG_UNKNOWN_TYPE})
            raise _CallOver
        self.report_type = report_type
        self.language = voice_session.language_of(start.get("language"))
        channel, caller_label = self._caller_identity(start)

        report = await self.service.create_report(report_type.id, channel, caller_label, "live")
        self.report_id = report.id
        await self.service.add_event(
            report.id, "call", "Call started", component="realtime", status="done"
        )

        try:
            await self._open_session()
        except Exception as error:  # provider text stays in the log
            logger.warning("call %s: realtime session failed: %s", report.id, type(error).__name__)
            logger.debug("realtime session error", exc_info=True)
            await self._send_json({"type": "error", "message": MSG_UNAVAILABLE})
            raise _CallOver from None

        self.clock = limits.CallClock()
        await self._send_json(
            {
                "type": "ready",
                "report_id": report.id,
                "report_type": report_type.model_dump(mode="json"),
            }
        )
        await self._send_fields(report)
        await self._create_response(voice_session.greeting_request(report_type, self.language))

        tasks = [
            asyncio.create_task(self._client_loop(), name="call-client"),
            asyncio.create_task(self._realtime_loop(), name="call-realtime"),
            asyncio.create_task(self._watchdog(), name="call-watchdog"),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                error = task.exception()
                if error is not None and not isinstance(error, (_CallOver, WebSocketDisconnect)):
                    raise error
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _caller_identity(self, start: dict[str, Any]) -> tuple[str, str]:
        """`channel` and `caller_label` of the start frame count only for the bridge key."""
        if auth_kind(self.ws) != "bearer" or key_scope(self.ws) != "bridge":
            return DEFAULT_CHANNEL, DEFAULT_CALLER_LABEL
        channel = start.get("channel")
        label = start.get("caller_label")
        channel = channel if channel in BRIDGE_CHANNELS else DEFAULT_CHANNEL
        default_label = "WhatsApp" if channel == "whatsapp" else DEFAULT_CALLER_LABEL
        if not isinstance(label, str) or not label.strip():
            return channel, default_label
        return channel, label.strip()[:40]

    async def _open_session(self) -> None:
        assert self.report_type is not None
        url, headers = voice_session.realtime_target(get_settings())
        self.realtime = await self.connect(url, headers)
        for variant in range(len(voice_session.SESSION_VARIANTS)):
            await self._to_realtime(
                voice_session.session_update(self.report_type, self.language, variant)
            )
            if await self._session_accepted():
                self._transcription_variant = variant
                return
            logger.info("call %s: session variant %d refused", self.report_id, variant)
        raise RuntimeError("no session variant accepted")

    async def _session_accepted(self) -> bool:
        assert self.realtime is not None
        deadline = time.monotonic() + SESSION_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("session.updated did not arrive")
            event = _parse(await asyncio.wait_for(self.realtime.recv(), remaining))
            if event.get("type") == "session.updated":
                return True
            if event.get("type") == "error":
                return False

    # ---- client -> realtime --------------------------------------------------------------

    async def _client_loop(self) -> None:
        while True:
            message = await self.ws.receive()
            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))
            data = message.get("bytes")
            if data:
                if len(data) <= MAX_AUDIO_FRAME_BYTES:
                    await self._to_realtime(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(data).decode("ascii"),
                        }
                    )
                continue
            frame = _parse(message.get("text"))
            kind = frame.get("type")
            if kind == "text":
                text = frame.get("text")
                if isinstance(text, str) and text.strip():
                    await self._typed_turn(text.strip()[:MAX_TEXT_CHARS])
            elif kind == "stop":
                self._outcome = "stopped"
                raise _CallOver

    async def _typed_turn(self, text: str) -> None:
        """Section 15.7: transcript out, the item in, then a response now or when one ends."""
        assert self.clock is not None
        self.clock.heard_caller()
        self._tool_rounds = 0
        self._turns += 1
        item_id = f"typed_{uuid.uuid4().hex[:12]}"
        await self._send_transcript("caller", text, True, item_id)
        await self._store_transcript("caller", text)
        await self._to_realtime(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
        if self._response_active:
            self._needs_response = True
        else:
            await self._create_response()

    async def _create_response(self, request: dict[str, Any] | None = None) -> None:
        self._response_active = True
        self._needs_response = False
        await self._send_state("thinking")
        await self._to_realtime(request or {"type": "response.create"})

    # ---- realtime -> client --------------------------------------------------------------

    async def _realtime_loop(self) -> None:
        assert self.realtime is not None
        while True:
            try:
                raw = await self.realtime.recv()
            except Exception as error:
                if self._saved:
                    raise _CallOver from None
                logger.warning("call %s: realtime closed: %s", self.report_id, type(error).__name__)
                await self._send_json({"type": "error", "message": MSG_LOST})
                raise _CallOver from None
            event = _parse(raw)
            if event:
                await self._on_event(event)

    async def _on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type", "")
        assert self.clock is not None

        if kind == "input_audio_buffer.speech_started":
            self.clock.heard_caller()
            self._tool_rounds = 0
            await self._send_json({"type": "interrupt"})
            self._speaking = False
            await self._send_state("listening")

        elif kind == "input_audio_buffer.speech_stopped":
            self.clock.heard_caller()

        elif kind == "input_audio_buffer.committed":
            item_id = event.get("item_id")
            if isinstance(item_id, str):
                self._pending_caller_items.add(item_id)

        elif kind == "conversation.item.input_audio_transcription.delta":
            item_id = str(event.get("item_id", ""))
            text = self._caller_text.get(item_id, "") + str(event.get("delta", ""))
            self._caller_text[item_id] = text
            await self._send_transcript("caller", text, False, item_id)

        elif kind == "conversation.item.input_audio_transcription.completed":
            item_id = str(event.get("item_id", ""))
            self._caller_text.pop(item_id, None)
            text = str(event.get("transcript") or "").strip()
            if text:
                self.clock.heard_caller()
                self._turns += 1
                await self._send_transcript("caller", text, True, item_id)
                await self._store_transcript("caller", text)
            await self._caller_item_settled(item_id)

        elif kind == "conversation.item.input_audio_transcription.failed":
            logger.warning("call %s: input transcription failed", self.report_id)
            await self._caller_item_settled(str(event.get("item_id", "")))
            await self._fall_back_transcription()

        elif kind == "response.created":
            self._response_active = True
            self._speaking = False
            await self._send_state("thinking")

        elif kind == "response.output_audio.delta":
            audio = event.get("delta")
            if isinstance(audio, str) and audio:
                if not self._speaking:
                    self._speaking = True
                    await self._send_state("speaking")
                await self._send_bytes(base64.b64decode(audio))

        elif kind == "response.output_audio_transcript.delta":
            item_id = str(event.get("item_id", ""))
            text = self._agent_text.get(item_id, "") + str(event.get("delta", ""))
            self._agent_text[item_id] = text
            await self._send_transcript("agent", text, False, item_id)

        elif kind == "response.output_audio_transcript.done":
            item_id = str(event.get("item_id", ""))
            text = str(event.get("transcript") or self._agent_text.get(item_id, "")).strip()
            self._agent_text.pop(item_id, None)
            if text:
                await self._send_transcript("agent", text, True, item_id)
                if self._pending_caller_items and len(self._held_agent_turns) < 2:
                    self._held_agent_turns.append(text)  # keep the stored order of turns
                else:
                    # No transcript is coming for those caller items: stop waiting for them.
                    self._pending_caller_items.clear()
                    await self._flush_held()
                    await self._store_transcript("agent", text)

        elif kind == "response.output_item.done":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                await self._tool_call(
                    str(item.get("call_id", "")),
                    str(item.get("name", "")),
                    str(item.get("arguments") or ""),
                )

        elif kind == "response.done":
            await self._response_done(event)

        elif kind == "error":
            error = event.get("error") or {}
            logger.warning("call %s: realtime error event: %s", self.report_id, error.get("code"))
            logger.debug("realtime error detail: %s", error.get("message"))

    async def _response_done(self, event: dict[str, Any]) -> None:
        self._response_active = False
        self._speaking = False
        status = (event.get("response") or {}).get("status")
        if self._saved and not self._needs_response:
            # The goodbye has been spoken (or the response after the save ended some other way).
            await self._close_saved()
            raise _CallOver
        if status == "cancelled":
            # The caller spoke over the agent: the next response comes from their turn.
            self._needs_response = self._needs_response and self._saved
        if self._needs_response and self._tool_rounds <= MAX_TOOL_ROUNDS_PER_TURN:
            await self._create_response()
            return
        if self._needs_response:
            logger.warning("call %s: tool loop guard tripped", self.report_id)
            self._needs_response = False
        await self._send_state("listening")

    async def _close_saved(self) -> None:
        await self._send_json({"type": "saved", "report_id": self.report_id})
        await self._send_state("done")

    async def _caller_item_settled(self, item_id: str) -> None:
        self._pending_caller_items.discard(item_id)
        if not self._pending_caller_items:
            await self._flush_held()

    async def _flush_held(self) -> None:
        held, self._held_agent_turns = self._held_agent_turns, []
        for text in held:
            await self._store_transcript("agent", text)

    async def _fall_back_transcription(self) -> None:
        """`gpt-4o-transcribe` accepted but not working on this resource: use `whisper-1`."""
        if voice_session.SESSION_VARIANTS[self._transcription_variant][0] == "whisper-1":
            return
        self._transcription_variant = 1
        await self._to_realtime(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "audio": {
                        "input": {
                            "transcription": {"model": "whisper-1", "language": self.language}
                        }
                    },
                },
            }
        )

    # ---- tools ---------------------------------------------------------------------------

    async def _tool_call(self, call_id: str, name: str, arguments: str) -> None:
        if not call_id or call_id in self._handled_calls:
            return
        self._handled_calls.add(call_id)
        self._tool_rounds += 1
        try:
            parsed = json.loads(arguments) if arguments else {}
            args = parsed if isinstance(parsed, dict) else {}
            if name == voice_session.SET_FIELDS:
                result = await self._set_fields(args)
            elif name == voice_session.SAVE_REPORT:
                result = await self._save_report()
            else:
                result = {"status": "error", "instruction": "No such tool. Continue the call."}
        except json.JSONDecodeError:
            result = {"status": "error", "instruction": "The arguments were not valid. Try again."}
        except Exception:
            logger.exception("call %s: tool %s failed", self.report_id, name)
            result = {
                "status": "error",
                "instruction": "Saving did not work. Tell the caller briefly and try once more.",
            }
        await self._to_realtime(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result, ensure_ascii=False),
                },
            }
        )
        self._needs_response = True  # sent when the running response is done (section 15.7)

    async def _set_fields(self, args: dict[str, Any]) -> dict[str, Any]:
        assert self.report_type is not None and self.report_id is not None
        names = {field.name for field in self.report_type.fields}
        values = {
            name: str(value).strip()
            for name, value in args.items()
            if name in names and value is not None and str(value).strip()
        }
        if not values:
            return {"status": "ok", "recorded": [], "missing_required": self._missing_labels()}
        report = await self.service.set_fields(self.report_id, values, "voice")
        self._values.update(values)
        await self._send_fields(report)
        missing = list(report.missing)
        return {
            "status": "ok",
            "recorded": sorted(values),
            "missing_required": missing,
            "instruction": (
                f"Ask for this one next, in one short question: {missing[0]}."
                if missing
                else "Nothing is missing. Read back a one-sentence summary and ask if it is right."
            ),
        }

    async def _save_report(self) -> dict[str, Any]:
        assert self.report_id is not None
        missing = self._missing_labels()
        if missing:
            return {
                "status": "not_saved",
                "missing_required": missing,
                "instruction": f"Not saved yet. Ask for this first: {missing[0]}.",
            }
        if not self._saved:
            await self._send_state("saving")
            await self._flush_held()
            await self._record_end()
            await self.service.finish_call(self.report_id)
            self._saved = True
            self._outcome = "saved"
        return {
            "status": "saved",
            "instruction": "Thank the caller in one short sentence and say goodbye.",
        }

    def _missing_labels(self) -> list[str]:
        assert self.report_type is not None
        return [
            field.label
            for field in self.report_type.fields
            if field.required and not self._values.get(field.name)
        ]

    async def _send_fields(self, report: Any) -> None:
        missing = list(report.missing)
        await self._send_json(
            {
                "type": "fields",
                "values": dict(self._values),
                "filled": report.filled,
                "total": report.total,
                "asking": missing[0] if missing else None,
            }
        )

    # ---- limits --------------------------------------------------------------------------

    async def _watchdog(self) -> None:
        assert self.clock is not None
        goodbye_deadline: float | None = None
        while True:
            await asyncio.sleep(
                min(WATCHDOG_INTERVAL_SECONDS, max(0.05, self.clock.seconds_left()))
            )
            if self._saved:
                # Do not cut the goodbye, but do not wait for it for ever either.
                goodbye_deadline = goodbye_deadline or time.monotonic() + GOODBYE_TIMEOUT_SECONDS
                if time.monotonic() >= goodbye_deadline:
                    await self._close_saved()
                    raise _CallOver
                continue
            reason = self.clock.ended_by()
            if reason:
                self._outcome = reason
                await self._send_json({"type": "error", "message": MSG_END[reason]})
                raise _CallOver

    # ---- end of call ---------------------------------------------------------------------

    async def _finish(self, seconds: float) -> None:
        if self.realtime is not None:
            with contextlib.suppress(Exception):
                await self.realtime.close()
        if self.report_id is not None:
            try:
                await self._settle_report(seconds)
            except Exception:
                logger.exception("call %s: could not settle the report", self.report_id)
        logger.info(
            "call ended: report=%s outcome=%s seconds=%.0f turns=%d fields=%d",
            self.report_id,
            self._outcome,
            seconds,
            self._turns,
            len(self._values),
        )
        if self.ws.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await self.ws.close(code=1000)

    async def _settle_report(self, seconds: float) -> None:
        assert self.report_id is not None
        if self._saved:
            return
        await self._flush_held()
        await self._record_end(DETAIL_CUT_OFF)
        if self._values:
            await self.service.finish_call(self.report_id)  # steps 3 to 6 on what was said
        else:
            await self.service.fail_report(self.report_id, DETAIL_CUT_OFF)

    async def _record_end(self, detail: str | None = None) -> None:
        """The timeline entry that closes the call, written before the pipeline starts."""
        assert self.report_id is not None
        await self.service.add_event(
            self.report_id,
            "call",
            "Call ended",
            detail=detail,
            component="realtime",
            status="done",
            ms=int((time.monotonic() - self._started) * 1000),
        )

    # ---- helpers -------------------------------------------------------------------------

    async def _store_transcript(self, role: str, text: str) -> None:
        if self.report_id is None:
            return
        try:
            await self.service.add_transcript(self.report_id, role, text)
        except Exception:
            logger.exception("call %s: transcript turn not stored", self.report_id)

    async def _to_realtime(self, payload: dict[str, Any]) -> None:
        assert self.realtime is not None
        await self.realtime.send(json.dumps(payload, ensure_ascii=False))

    async def _refuse(self, message: str) -> None:
        await self._send_json({"type": "error", "message": message})
        with contextlib.suppress(Exception):
            await self.ws.close(code=1000)

    async def _send_state(self, value: str) -> None:
        await self._send_json({"type": "state", "value": value})

    async def _send_transcript(self, role: str, text: str, final: bool, item_id: str) -> None:
        await self._send_json(
            {"type": "transcript", "role": role, "text": text, "final": final, "item_id": item_id}
        )

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if self.ws.application_state != WebSocketState.CONNECTED:
            return
        async with self._send_lock:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await self.ws.send_text(json.dumps(payload, ensure_ascii=False))

    async def _send_bytes(self, data: bytes) -> None:
        if self.ws.application_state != WebSocketState.CONNECTED:
            return
        async with self._send_lock:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await self.ws.send_bytes(data)


def _parse(raw: object) -> dict[str, Any]:
    """A JSON object from a text frame; anything else is an empty dict."""
    if not isinstance(raw, (str, bytes)) or not raw:
        return {}
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
