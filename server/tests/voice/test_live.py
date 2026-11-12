"""A real call by typed turns: the app's own WebSocket, the real Realtime deployment and the
real reports service. Synthetic content only.

    uv run pytest tests/voice/test_live.py -m live -s
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from tests.voice.conftest import WS_PATH, read

CONFIRM = "Yes, that is correct, nothing to add. Please save the report."
UNKNOWN = "I do not know that. Mark it as not known."

CALLS: dict[str, dict[str, Any]] = {
    "turvallisuushavainto": {
        "story": [
            "I was on the loading dock of warehouse two and saw paint cans stacked too high on "
            "the top shelf. One can fell down right next to a forklift driver.",
            "Nobody was hurt. It happened today around half past nine. I moved the driver away "
            "and put barrier tape around the shelf.",
        ],
        "answers": {
            "Date": "It was today.",
            "Location": "The loading dock of warehouse two.",
            "What was observed": "A paint can fell from an overloaded top shelf next to a driver.",
            "Hazard type": "A falling object.",
            "Severity": "It was a near miss.",
        },
    },
    "huoltoraportti": {
        "story": [
            "I serviced the conveyor belt on packing line three today. It had stopped because "
            "the drive belt was slipping.",
            "I replaced the drive belt and tightened the tensioner. It took two hours and the "
            "line runs normally again.",
        ],
        "answers": {
            "Date": "Today.",
            "Equipment": "The conveyor belt on packing line three.",
            "Fault or reason for visit": "The drive belt was slipping and the line stopped.",
            "Work carried out": "I replaced the drive belt and tightened the tensioner.",
            "Outcome": "It is fixed.",
        },
    },
}


def _turn(ws: Any, text: str | None, log: list[str]) -> dict[str, Any]:
    """Send one typed turn (or none, for the greeting) and read until the agent has finished.

    Returns what happened: the last `fields` message, whether `saved` came, audio bytes,
    milliseconds to the first audio and to the end of the turn."""
    started = time.monotonic()
    if text is not None:
        ws.send_json({"type": "text", "text": text})
    result: dict[str, Any] = {"fields": None, "saved": None, "audio": 0, "first_audio_ms": None}
    while True:
        message = read(ws)
        kind = message["type"]
        if kind == "audio":
            result["audio"] += len(message["bytes"])
            if result["first_audio_ms"] is None:
                result["first_audio_ms"] = int((time.monotonic() - started) * 1000)
        elif kind == "transcript" and message["final"]:
            log.append(f"{message['role']:>6}: {message['text']}")
        elif kind == "fields":
            result["fields"] = message
        elif kind == "saved":
            result["saved"] = message["report_id"]
        elif kind == "error":
            log.append(f" error: {message['message']}")
            result["error"] = message["message"]
        elif kind == "close" or (kind == "state" and message["value"] == "listening"):
            result["closed"] = kind == "close"
            result["turn_ms"] = int((time.monotonic() - started) * 1000)
            return result


@pytest.mark.live
@pytest.mark.parametrize("type_id", ["turvallisuushavainto", "huoltoraportti"])
def test_live_call_by_typed_turns(client, type_id):
    call = CALLS[type_id]
    log: list[str] = []
    timings: list[tuple[str, int | None, int]] = []
    fields: dict[str, Any] | None = None
    saved = None
    audio = 0
    with client.websocket_connect(WS_PATH) as ws:
        ws.send_json({"type": "start", "type_id": type_id, "language": "en"})
        ready = read(ws)
        assert ready["type"] == "ready", ready
        report_id = ready["report_id"]
        labels = {field["label"] for field in ready["report_type"]["fields"]}
        assert set(call["answers"]) <= labels

        greeting = _turn(ws, None, log)
        timings.append(("greeting", greeting["first_audio_ms"], greeting["turn_ms"]))
        audio += greeting["audio"]
        assert greeting["audio"] > 0
        assert "AI assistant of GAIK" in log[0]
        fields = greeting["fields"]

        script = list(call["story"])
        for number in range(14):
            if script:
                text = script.pop(0)
            elif fields and fields["asking"]:
                text = call["answers"].get(fields["asking"], UNKNOWN)
            else:
                text = CONFIRM
            result = _turn(ws, text, log)
            timings.append((f"turn {number + 1}", result["first_audio_ms"], result["turn_ms"]))
            audio += result["audio"]
            fields = result["fields"] or fields
            saved = result["saved"] or saved
            if result["closed"]:
                break

    print(f"\n--- {type_id}: transcript ---")
    print("\n".join(log))
    print("--- timings (ms to first audio, ms to end of turn) ---")
    for name, first, total in timings:
        print(f"{name:>9}: {first} / {total}")
    print(f"fields: {fields}")
    print(f"agent audio: {audio} bytes = {audio / 48000:.1f} s")

    assert saved == report_id
    assert fields is not None and fields["filled"] == fields["total"] and fields["asking"] is None
    assert audio > 48000

    # the pipeline (steps 3 to 6) runs in the background on the call transcript
    deadline = time.monotonic() + 180
    report: dict[str, Any] = {}
    while time.monotonic() < deadline:
        report = client.get(f"/api/reports/{report_id}").json()
        if report.get("status") in ("needs_approval", "failed", "sent"):
            break
        time.sleep(1)
    print(f"report status: {report.get('status')}, {report.get('filled')}/{report.get('total')}")
    print("timeline:", [(e["kind"], e["status"], e["ms"]) for e in report.get("timeline", [])])
    for field in report.get("fields", []):
        print(f"  {field['label']}: {field['value']} [{field['source']}]")
    assert report["channel"] == "call" and report["caller_label"] == "Browser call"
    assert report["status"] == "needs_approval"
    assert report["filled"] == report["total"]
    assert len(report["transcript"]) >= 6
