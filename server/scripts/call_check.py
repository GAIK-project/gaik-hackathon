"""Check the call WebSocket of a running server with real audio, without a microphone.

Streams a PCM16 24 kHz mono file at real-time pace after the greeting, then silence, and checks
that the caller's speech comes back as a transcript and that the agent answers with audio.

    uv run python scripts/call_check.py --url ws://127.0.0.1:8792/api/call/ws
    uv run python scripts/call_check.py --pcm speech.pcm --language en

Without `--pcm` two sentences are synthesised with the TTS settings (`TTS_ENDPOINT`,
`AZURE_TTS_MODEL`) and converted with ffmpeg. The bridge key is sent when it is set.
Synthetic content only. Exit code 0 when both checks pass.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.files import ffmpeg_path  # noqa: E402
from app.core.settings import get_settings  # noqa: E402

CHUNK_BYTES = 1920  # 40 ms of PCM16 at 24 kHz
CHUNK_SECONDS = 0.04
LEAD_IN_CHUNKS = 15
DEFAULT_TEXT = (
    "Lastauslaiturilla varaston kaksi ylähyllyltä putosi maalipurkki trukinkuljettajan viereen. "
    "Kukaan ei loukkaantunut, ja eristin alueen nauhalla."
)


def synthesise(text: str, folder: Path) -> Path:
    """Text to a PCM16 24 kHz mono file with the TTS deployment and ffmpeg."""
    settings = get_settings()
    ffmpeg = ffmpeg_path()
    if not settings.tts_endpoint or not ffmpeg:
        raise SystemExit("TTS_ENDPOINT or ffmpeg is missing: pass --pcm instead")
    body = {
        "model": settings.azure_tts_model or "tts-hd",
        "input": text,
        "voice": "alloy",
        "response_format": "wav",
    }
    response = None
    for key in (settings.azure_api_key, settings.azure_realtime_api_key):
        if not key:
            continue
        response = httpx.post(
            settings.tts_endpoint, headers={"api-key": key}, json=body, timeout=60
        )
        if response.status_code != 401:
            break
    if response is None or response.status_code != 200:
        status = response.status_code if response is not None else "no key"
        raise SystemExit(f"TTS failed: {status}")
    wav = folder / "speech.wav"
    pcm = folder / "speech.pcm"
    wav.write_bytes(response.content)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(wav),
            "-f",
            "s16le",
            "-ar",
            "24000",
            "-ac",
            "1",
            str(pcm),
        ],
        check=True,
    )
    return pcm


async def check(url: str, pcm: bytes, type_id: str, language: str, wait_seconds: float) -> bool:
    settings = get_settings()
    headers = {}
    if settings.hack_bridge_key:
        headers["Authorization"] = f"Bearer {settings.hack_bridge_key}"
    started = time.monotonic()
    state = {"phase": "greeting", "speech_end": 0.0}
    caller: list[str] = []
    agent: list[str] = []
    audio = {"greeting": 0, "answer": 0}
    marks: dict[str, int] = {}

    def mark(name: str, since: float) -> None:
        marks.setdefault(name, int((time.monotonic() - since) * 1000))

    async with websockets.connect(url, additional_headers=headers, max_size=None) as ws:
        await ws.send(json.dumps({"type": "start", "type_id": type_id, "language": language}))
        greeted = asyncio.Event()
        answered = asyncio.Event()

        async def reader() -> None:
            async for message in ws:
                if isinstance(message, bytes):
                    if state["phase"] == "greeting":
                        audio["greeting"] += len(message)
                        mark("greeting_first_audio_ms", started)
                    elif caller:
                        audio["answer"] += len(message)
                        mark("answer_first_audio_ms", state["speech_end"])
                    continue
                event = json.loads(message)
                kind = event.get("type")
                if kind == "ready":
                    mark("ready_ms", started)
                elif kind == "transcript" and event["final"]:
                    (caller if event["role"] == "caller" else agent).append(event["text"])
                    print(f"{event['role']:>6}: {event['text']}")
                    if event["role"] == "caller":
                        mark("caller_transcript_ms", state["speech_end"])
                elif kind == "interrupt":
                    print("     (interrupt)")
                elif kind == "error":
                    print(f" error: {event['message']}")
                    answered.set()
                    greeted.set()
                elif kind == "state" and event["value"] == "listening":
                    if state["phase"] == "greeting":
                        greeted.set()
                    elif caller and audio["answer"]:
                        answered.set()

        task = asyncio.create_task(reader())
        try:
            await asyncio.wait_for(greeted.wait(), 30)
            state["phase"] = "speech"
            silence = bytes(CHUNK_BYTES)
            chunks = [silence] * LEAD_IN_CHUNKS
            chunks += [pcm[i : i + CHUNK_BYTES] for i in range(0, len(pcm), CHUNK_BYTES)]
            next_at = time.monotonic()
            for chunk in chunks:
                await ws.send(chunk)
                next_at += CHUNK_SECONDS
                await asyncio.sleep(max(0.0, next_at - time.monotonic()))
            state["speech_end"] = time.monotonic()
            deadline = state["speech_end"] + wait_seconds
            while not answered.is_set() and time.monotonic() < deadline:
                await ws.send(silence)
                next_at += CHUNK_SECONDS
                await asyncio.sleep(max(0.0, next_at - time.monotonic()))
            await ws.send(json.dumps({"type": "stop"}))
            await asyncio.wait_for(task, 10)
        finally:
            task.cancel()

    print(f"speech sent: {len(pcm) / 48000:.1f} s; timings: {marks}")
    greeting_s, answer_s = audio["greeting"] / 48000, audio["answer"] / 48000
    print(f"agent audio: greeting {greeting_s:.1f} s, answer {answer_s:.1f} s")
    ok_transcript = bool(caller and caller[0].strip())
    ok_audio = audio["answer"] > 0
    print(f"caller transcript: {'ok' if ok_transcript else 'MISSING'}")
    print(f"agent audio: {'ok' if ok_audio else 'MISSING'}")
    return ok_transcript and ok_audio


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="ws://127.0.0.1:8792/api/call/ws")
    parser.add_argument("--pcm", type=Path, help="PCM16 little-endian, mono, 24 kHz")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="what to synthesise without --pcm")
    parser.add_argument("--type-id", default="turvallisuushavainto")
    parser.add_argument("--language", default="fi", choices=["en", "fi", "sv"])
    parser.add_argument("--wait", type=float, default=30.0, help="seconds to wait for the answer")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        pcm_path = args.pcm or synthesise(args.text, Path(folder))
        pcm = pcm_path.read_bytes()
    return 0 if asyncio.run(check(args.url, pcm, args.type_id, args.language, args.wait)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
