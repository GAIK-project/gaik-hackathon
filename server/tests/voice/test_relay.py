"""The call relay against a fake Realtime server and a fake reports service (no network)."""

from __future__ import annotations

import base64
import json
import time

from app.core import limits
from app.voice import relay
from tests.voice.conftest import START, WS_PATH, read, read_until, wait_for
from tests.voice.fakes import call_tool, speak

GREETING = speak("Hello, I'm the AI assistant of GAIK.", "item_greeting")


def _types(messages: list[dict]) -> list[str]:
    return [message["type"] for message in messages]


def _open(ws) -> dict:
    """Send the start frame and read up to the end of the greeting. Returns `ready`."""
    ws.send_json(START)
    seen = read_until(ws, "state", value="listening")
    ready = next(message for message in seen if message["type"] == "ready")
    assert _types(seen)[0] == "ready"
    return ready


def test_full_call_by_typed_turns(client, reports, realtime):
    fake = realtime(
        [
            GREETING,
            call_tool(
                "set_fields",
                {"description": "Loose scaffold plank", "location": "Hall B", "x": "1"},
                "c1",
            ),
            speak("How serious is it?", "item_q"),
            call_tool("set_fields", {"severity": "high"}, "c2"),
            speak("A loose scaffold plank in hall B, high severity. Is that right?", "item_s"),
            call_tool("save_report", {"summary": "A loose plank."}, "c3"),
            speak("Thank you, goodbye.", "item_bye"),
        ]
    )
    with client.websocket_connect(WS_PATH) as ws:
        ws.send_json(START)
        opening = read_until(ws, "state", value="listening")
        ready = opening[0]
        assert ready["type"] == "ready" and ready["report_type"]["id"] == "turvallisuushavainto"
        report_id = ready["report_id"]
        assert opening[1] == {
            "type": "fields",
            "values": {},
            "filled": 0,
            "total": 3,
            "asking": "What happened",
        }
        assert "audio" in _types(opening)
        greeting = [m for m in opening if m["type"] == "transcript"]
        assert [m["final"] for m in greeting] == [False, False, True]
        assert greeting[1]["text"] == greeting[2]["text"] == "Hello, I'm the AI assistant of GAIK."
        assert {m["item_id"] for m in greeting} == {"item_greeting"}
        states = [m["value"] for m in opening if m["type"] == "state"]
        assert states == ["thinking", "thinking", "speaking", "listening"]

        ws.send_json({"type": "text", "text": "A scaffold plank is loose in hall B."})
        turn = read_until(ws, "state", value="listening")
        assert turn[0]["type"] == "transcript" and turn[0]["role"] == "caller" and turn[0]["final"]
        fields = next(m for m in turn if m["type"] == "fields")
        assert fields == {
            "type": "fields",
            "values": {"description": "Loose scaffold plank", "location": "Hall B"},
            "filled": 2,
            "total": 3,
            "asking": "Severity",
        }

        ws.send_json({"type": "text", "text": "It is serious."})
        turn = read_until(ws, "state", value="listening")
        fields = next(m for m in turn if m["type"] == "fields")
        assert fields["filled"] == 3 and fields["asking"] is None

        ws.send_json({"type": "text", "text": "Yes, that is right."})
        end = read_until(ws, "close")
        kinds = [
            (m["type"], m.get("value")) for m in end if m["type"] in ("state", "saved", "close")
        ]
        assert ("state", "saving") in kinds
        assert kinds[-3:] == [("saved", None), ("state", "done"), ("close", None)]
        assert end[-3] == {"type": "saved", "report_id": report_id}
        assert end[-1]["code"] == 1000
        # the goodbye is heard before `saved`
        assert _types(end).index("audio") < _types(end).index("saved")

    wait_for(lambda: fake.closed)
    assert reports.finished == [report_id] and reports.failed == []
    state = reports.reports[report_id]
    assert state["channel"] == "call" and state["caller_label"] == "Browser call"
    assert state["values"] == {
        "description": "Loose scaffold plank",
        "location": "Hall B",
        "severity": "high",
    }
    assert [role for role, _ in state["transcript"]] == [
        "agent", "caller", "agent", "caller", "agent", "caller", "agent",
    ]  # fmt: skip
    assert [event.label for event in state["events"]] == ["Call started", "Call ended"]

    # what the Realtime server saw
    kinds = [event["type"] for event in fake.sent]
    assert kinds[0] == "session.update" and kinds[1] == "response.create"
    assert "Say exactly" in fake.sent[1]["response"]["instructions"]
    typed = [e for e in fake.sent_of("conversation.item.create") if e["item"]["type"] == "message"]
    assert [e["item"]["content"][0]["type"] for e in typed] == ["input_text"] * 3
    outputs = [
        e
        for e in fake.sent_of("conversation.item.create")
        if e["item"]["type"] == "function_call_output"
    ]
    assert [e["item"]["call_id"] for e in outputs] == ["c1", "c2", "c3"]
    first = json.loads(outputs[0]["item"]["output"])
    assert first["recorded"] == ["description", "location"] and first["missing_required"] == [
        "Severity"
    ]
    assert json.loads(outputs[2]["item"]["output"])["status"] == "saved"
    assert len(fake.sent_of("response.create")) == 7
    # every tool output is followed by a response.create, never preceded by one for that round
    for output in outputs:
        assert kinds[fake.sent.index(output) + 1] == "response.create"


def test_typed_turn_waits_for_the_running_response(client, reports, realtime):
    fake = realtime([[{"type": "response.created", "response": {}}], speak("I see.", "item_b")])
    with client.websocket_connect(WS_PATH) as ws:
        ws.send_json(START)
        read_until(ws, "fields")
        ws.send_json({"type": "text", "text": "Hello there."})
        read_until(ws, "transcript", role="caller")
        wait_for(lambda: len(fake.sent_of("conversation.item.create")) == 1)
        time.sleep(0.1)
        assert len(fake.sent_of("response.create")) == 1  # only the greeting so far
        fake.emit({"type": "response.done", "response": {"status": "completed"}})
        read_until(ws, "state", value="listening")
        assert [e["type"] for e in fake.sent][-2:] == [
            "conversation.item.create",
            "response.create",
        ]
        ws.send_json({"type": "stop"})
        read_until(ws, "close")


def test_audio_both_ways_barge_in_and_transcript_order(client, reports, realtime):
    fake = realtime([GREETING])
    with client.websocket_connect(WS_PATH) as ws:
        report_id = _open(ws)["report_id"]
        frame = b"\x02\x00" * 480
        ws.send_bytes(frame)
        wait_for(lambda: len(fake.sent_of("input_audio_buffer.append")) == 1)
        assert base64.b64decode(fake.sent_of("input_audio_buffer.append")[0]["audio"]) == frame

        fake.emit({"type": "input_audio_buffer.speech_started"})
        seen = read_until(ws, "state", value="listening")
        assert seen[0] == {"type": "interrupt"}

        # the agent answers before the caller's transcript arrives: stored order is still right
        fake.emit(
            {"type": "input_audio_buffer.committed", "item_id": "item_u"},
            *speak("Where was it?", "item_c"),
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "item_id": "item_u",
                "delta": "In ",
            },
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item_u",
                "transcript": "In hall B.",
            },
        )
        seen = read_until(ws, "transcript", role="caller", final=True)
        assert seen[-1]["text"] == "In hall B." and seen[-1]["item_id"] == "item_u"
        partial = {"type": "transcript", "role": "caller", "text": "In ", "final": False}
        assert seen[-2] == {**partial, "item_id": "item_u"}
        wait_for(lambda: len(reports.reports[report_id]["transcript"]) == 3)
        assert reports.reports[report_id]["transcript"][1:] == [
            ("caller", "In hall B."),
            ("agent", "Where was it?"),
        ]


def test_disconnect_with_fields_runs_the_pipeline(client, reports, realtime):
    realtime([GREETING, call_tool("set_fields", {"location": "Hall B"}, "c1"), speak("And?", "i2")])
    with client.websocket_connect(WS_PATH) as ws:
        report_id = _open(ws)["report_id"]
        ws.send_json({"type": "text", "text": "It was in hall B."})
        read_until(ws, "state", value="listening")
    wait_for(lambda: reports.finished == [report_id])
    assert reports.failed == []
    assert limits.active_calls() == 0


def test_disconnect_without_fields_fails_the_report(client, reports, realtime):
    realtime([GREETING])
    with client.websocket_connect(WS_PATH) as ws:
        report_id = _open(ws)["report_id"]
    wait_for(lambda: reports.failed == [(report_id, "The call was cut off")])
    assert reports.finished == []
    wait_for(lambda: limits.active_calls() == 0)


def test_stop_frame_ends_the_call(client, reports, realtime):
    fake = realtime([GREETING])
    with client.websocket_connect(WS_PATH) as ws:
        report_id = _open(ws)["report_id"]
        ws.send_json({"type": "stop"})
        assert read_until(ws, "close")[-1]["code"] == 1000
    wait_for(lambda: fake.closed and reports.failed == [(report_id, "The call was cut off")])


def test_save_is_refused_while_a_required_field_is_missing(client, reports, realtime):
    fake = realtime(
        [GREETING, call_tool("save_report", {"summary": "x"}, "c1"), speak("Where?", "i2")]
    )
    with client.websocket_connect(WS_PATH) as ws:
        _open(ws)
        ws.send_json({"type": "text", "text": "Save it."})
        seen = read_until(ws, "state", value="listening")
        assert "saved" not in _types(seen)
        output = json.loads(fake.sent_of("conversation.item.create")[1]["item"]["output"])
        assert output["status"] == "not_saved" and output["missing_required"][0] == "What happened"
        assert reports.finished == []


def test_unknown_tool_and_bad_arguments_do_not_break_the_call(client, reports, realtime):
    bad = call_tool("set_fields", {}, "c2")
    bad[1]["item"]["arguments"] = "{not json"
    fake = realtime([GREETING, call_tool("send_mail", {}, "c1"), bad, speak("Sorry?", "i2")])
    with client.websocket_connect(WS_PATH) as ws:
        _open(ws)
        ws.send_json({"type": "text", "text": "Hello."})
        read_until(ws, "state", value="listening")
        outputs = [
            json.loads(e["item"]["output"])
            for e in fake.sent_of("conversation.item.create")
            if e["item"]["type"] == "function_call_output"
        ]
        assert [o["status"] for o in outputs] == ["error", "error"]


def test_fourth_call_is_refused(client, reports, realtime):
    realtime([GREETING])
    for _ in range(limits.MAX_CALLS):
        assert limits.acquire_call()
    with client.websocket_connect(WS_PATH) as ws:
        ws.send_json(START)
        assert read(ws) == {"type": "error", "message": relay.MSG_BUSY}
        assert read(ws)["type"] == "close"
    assert reports.reports == {}
    assert limits.active_calls() == limits.MAX_CALLS


def test_silence_ends_the_call(client, reports, realtime, monkeypatch):
    monkeypatch.setattr(relay, "WATCHDOG_INTERVAL_SECONDS", 0.02)
    realtime([GREETING])
    with client.websocket_connect(WS_PATH) as ws:
        report_id = _open(ws)["report_id"]
        real = time.monotonic
        monkeypatch.setattr(limits, "_now", lambda: real() + limits.CALL_SILENCE_SECONDS + 1)
        seen = read_until(ws, "close")
        assert seen[-2] == {"type": "error", "message": relay.MSG_END["silence"]}
    wait_for(lambda: reports.failed == [(report_id, "The call was cut off")])


def test_six_minutes_end_the_call(client, reports, realtime, monkeypatch):
    monkeypatch.setattr(relay, "WATCHDOG_INTERVAL_SECONDS", 0.02)
    realtime([GREETING])
    with client.websocket_connect(WS_PATH) as ws:
        _open(ws)
        real = time.monotonic
        monkeypatch.setattr(limits, "_now", lambda: real() + limits.CALL_MAX_SECONDS + 1)
        seen = read_until(ws, "close")
        assert seen[-2] == {"type": "error", "message": relay.MSG_END["duration"]}


def test_channel_and_caller_label_only_for_the_bridge(client, bridge_client, reports, realtime):
    start = {**START, "channel": "whatsapp", "caller_label": "WhatsApp call"}
    realtime([GREETING])
    with client.websocket_connect(WS_PATH) as ws:
        ws.send_json(start)
        browser_id = read_until(ws, "ready")[-1]["report_id"]
    realtime([GREETING])
    with bridge_client.websocket_connect(WS_PATH) as ws:
        ws.send_json(start)
        bridge_id = read_until(ws, "ready")[-1]["report_id"]
    assert (
        reports.reports[browser_id]["channel"],
        reports.reports[browser_id]["caller_label"],
    ) == (
        "call",
        "Browser call",
    )
    assert (reports.reports[bridge_id]["channel"], reports.reports[bridge_id]["caller_label"]) == (
        "whatsapp",
        "WhatsApp call",
    )


def test_socket_needs_credentials(anon_client, mcp_client, reports, realtime):
    realtime([GREETING])
    for refused in (anon_client, mcp_client):
        with refused.websocket_connect(WS_PATH) as ws:
            message = ws.receive()
            assert message["type"] == "websocket.close" and message["code"] == 4401
    assert reports.reports == {}


def test_bad_start_unknown_type_and_models_off(client, reports, realtime, monkeypatch):
    realtime([GREETING])
    with client.websocket_connect(WS_PATH) as ws:
        ws.send_json({"type": "text", "text": "hello"})
        assert read(ws) == {"type": "error", "message": relay.MSG_BAD_START}
        assert read(ws)["type"] == "close"
    with client.websocket_connect(WS_PATH) as ws:
        ws.send_json({**START, "type_id": "nope"})
        assert read(ws) == {"type": "error", "message": relay.MSG_UNKNOWN_TYPE}
        assert read(ws)["type"] == "close"
    assert reports.reports == {} and limits.active_calls() == 0
    monkeypatch.setenv("HACK_MODELS_OFF", "1")
    from app.core.settings import get_settings

    get_settings.cache_clear()
    with client.websocket_connect(WS_PATH) as ws:
        assert read(ws) == {"type": "error", "message": relay.MSG_MODELS_OFF}


def test_language_comes_from_the_start_frame_and_session_falls_back(client, reports, realtime):
    fake = realtime([GREETING], refuse_sessions=1)
    with client.websocket_connect(WS_PATH) as ws:
        ws.send_json({**START, "language": "fi"})
        read_until(ws, "state", value="listening")
    updates = fake.sent_of("session.update")
    assert [u["session"]["audio"]["input"]["transcription"] for u in updates] == [
        {"model": "gpt-4o-transcribe", "language": "fi"},
        {"model": "whisper-1", "language": "fi"},
    ]
    assert "Speak only Finnish" in updates[0]["session"]["instructions"]
    assert "tekoälyavustaja" in fake.sent_of("response.create")[0]["response"]["instructions"]
    assert fake.url.startswith("wss://") and "/openai/v1/realtime?model=" in fake.url
    assert list(fake.headers) == ["api-key"]


def test_transcription_failure_switches_to_whisper(client, reports, realtime):
    fake = realtime([GREETING])
    with client.websocket_connect(WS_PATH) as ws:
        _open(ws)
        fake.emit({"type": "conversation.item.input_audio_transcription.failed", "item_id": "u1"})
        wait_for(lambda: len(fake.sent_of("session.update")) == 2)
        assert (
            fake.sent_of("session.update")[1]["session"]["audio"]["input"]["transcription"]["model"]
            == "whisper-1"
        )


def test_provider_drop_tells_the_caller_and_keeps_no_provider_text(client, reports, realtime):
    fake = realtime([GREETING])
    with client.websocket_connect(WS_PATH) as ws:
        report_id = _open(ws)["report_id"]
        fake.emit({"type": "error", "error": {"code": "x", "message": "secret provider text"}})
        fake.drop()
        seen = read_until(ws, "close")
        assert seen[-2] == {"type": "error", "message": relay.MSG_LOST}
        assert "secret provider text" not in json.dumps(seen)
    wait_for(lambda: reports.failed == [(report_id, "The call was cut off")])


def test_realtime_unavailable(client, reports, app):
    from app.voice import router as voice_router

    async def refuse(url, headers):
        raise OSError("no route")

    app.dependency_overrides[voice_router.realtime_connect] = lambda: refuse
    with client.websocket_connect(WS_PATH) as ws:
        ws.send_json(START)
        assert read(ws) == {"type": "error", "message": relay.MSG_UNAVAILABLE}
        assert read(ws)["type"] == "close"
    wait_for(lambda: len(reports.failed) == 1 and limits.active_calls() == 0)
