"""Live check of the services the app depends on. One line per service.

    uv run python scripts/check_env.py            (from server/)

Each line is `ok` with a short fact, or `FAIL` with the error class, the HTTP status and the
provider's error code. No value of any setting is ever printed: whatever goes to the terminal
is first scrubbed of every configured value, which is replaced by `<NAME>`.

Checks: (a) one structured chat call, (b) one embedding through gaik `Embedder`,
(c) transcription of a public synthetic sample through gaik `Transcriber`, (d) one
`JevClassifier` call through gaik-decide, (e) the Azure Realtime WebSocket up to
`session.created`. Exit code 1 when any check fails.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import logging
import sys
import tempfile
import time
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.simplefilter("ignore")
logging.disable(logging.CRITICAL)  # provider messages may echo configuration

from pydantic import BaseModel  # noqa: E402

from app.core import llm  # noqa: E402
from app.core.settings import REPO_ROOT, Settings, get_settings  # noqa: E402

SAMPLE_AUDIO = (
    REPO_ROOT.parent
    / "gaik-toolkit"
    / "implementation_layer"
    / "no-code-assets"
    / "samples"
    / "construction-diary-creation"
    / "fin-example-1.mp3"
)
_MIN_REDACT_LENGTH = 4
_DEPLOYMENT_NAMES = frozenset(
    {
        "azure_deployment",
        "azure_transcription_model",
        "embedding_model",
        "azure_realtime_deployment",
        "azure_tts_model",
    }
)


class Ping(BaseModel):
    answer: str


class Failure(Exception):
    """A check that failed without an exception of its own."""


def redactor(settings: Settings) -> Callable[[str], str]:
    """A function that replaces every configured value in a text with `<NAME>`."""
    # Deployment names are short and only ever appear in provider messages; every other value
    # is scrubbed from 8 characters up, so a short user name cannot be guessed from a
    # replacement inside an unrelated word.
    pairs = [
        (value, f"<{name.upper()}>")
        for name, value in settings.model_dump().items()
        if isinstance(value, str)
        and len(value) >= (_MIN_REDACT_LENGTH if name in _DEPLOYMENT_NAMES else 8)
    ]
    for name in ("azure_endpoint", "azure_realtime_endpoint", "gaik_decide_url", "tts_endpoint"):
        host = urlsplit(getattr(settings, name)).hostname
        if host:
            pairs.append((host, f"<{name.upper()} host>"))
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)

    def redact(text: str) -> str:
        for value, label in pairs:
            text = text.replace(value, label)
        return text

    return redact


def describe(error: BaseException) -> str:
    """Error class, HTTP status and provider code, then the first line of the message."""
    cause = error.__cause__ if type(error).__name__ == "AppError" and error.__cause__ else error
    parts = [type(cause).__name__]
    status = getattr(cause, "status_code", None)
    response = getattr(cause, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    if status is not None:
        parts.append(f"status={status}")
    code = getattr(cause, "code", None)
    if isinstance(code, str) and code:
        parts.append(f"code={code}")
    message = (str(cause).strip().splitlines() or [""])[0]
    if message:
        parts.append(message[:160])
    return " ".join(parts)


def quiet(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call gaik with its prints swallowed."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def check_chat(settings: Settings) -> str:
    result = llm.structured_chat_sync("Answer with one word.", "Say ok.", Ping)
    return f"structured answer received ({len(result.answer)} characters)"


def check_embedding(settings: Settings) -> str:
    vector = quiet(lambda: llm.embedder().embed_query("Työmaan turvallisuushavainto"))
    if not vector:
        raise Failure("empty vector")
    return f"{len(vector)} dimensions"


def check_transcription(settings: Settings, audio: Path) -> str:
    if not audio.is_file():
        raise Failure("sample audio file not found (pass --audio <file>)")

    def run() -> str:
        from gaik.software_components.transcriber import Transcriber

        with tempfile.TemporaryDirectory() as workdir:
            result = Transcriber(llm.gaik_config(), output_dir=workdir).transcribe(audio)
            return (result.enhanced_transcript or result.raw_transcript or "").strip()

    text = quiet(run)
    if not text or "[Transcription failed" in text:
        raise Failure("the transcriber returned no text")
    return f"{len(text)} characters: {' '.join(text.split())[:80]}"


def check_jev(settings: Settings) -> str:
    result = quiet(
        lambda: llm.jev_classifier().classify(
            "Nosturin nostovaijeri on pahasti kulunut ja se on vaihdettava ennen seuraavaa nostoa.",
            {
                "kiireellinen": "Vaatii toimia heti, vaara ihmisille tai työn pysähtyminen",
                "normaali": "Hoidetaan tavallisessa aikataulussa",
                "ei kiireellinen": "Tiedoksi, ei vaadi toimia lähiaikoina",
            },
            instructions="Kuinka kiireellinen tämä työmaan havainto on?",
        )
    )
    if result.get("error"):
        raise Failure(str(result["error"]))
    return (
        f"label={result.get('class')} confidence={result.get('confidence')} "
        f"model={result.get('model')}"
    )


async def _realtime(settings: Settings) -> str:
    import websockets

    missing = settings.missing("azure_realtime_endpoint", "azure_realtime_deployment")
    if missing:
        raise Failure(f"not set: {', '.join(missing)}")
    key = settings.azure_realtime_api_key or settings.azure_api_key
    endpoint = settings.azure_realtime_endpoint
    host = urlsplit(endpoint if "://" in endpoint else f"https://{endpoint}").netloc
    url = f"wss://{host}/openai/v1/realtime?model={quote(settings.azure_realtime_deployment)}"
    async with websockets.connect(
        url, additional_headers={"api-key": key}, open_timeout=20, max_size=None
    ) as socket:
        event = json.loads(await asyncio.wait_for(socket.recv(), timeout=20))
    if event.get("type") != "session.created":
        code = (event.get("error") or {}).get("code")
        raise Failure(f"first event was {event.get('type')} (code {code})")
    return "session.created received"


def check_realtime(settings: Settings) -> str:
    return asyncio.run(_realtime(settings))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--audio", type=Path, default=SAMPLE_AUDIO, help="audio file for (c)")
    parser.add_argument("--only", nargs="*", help="run only these checks, by name")
    arguments = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")

    settings = get_settings()
    redact = redactor(settings)
    checks: list[tuple[str, Callable[[], str]]] = [
        ("chat", lambda: check_chat(settings)),
        ("embedding", lambda: check_embedding(settings)),
        ("transcription", lambda: check_transcription(settings, arguments.audio)),
        ("jev", lambda: check_jev(settings)),
        ("realtime", lambda: check_realtime(settings)),
    ]
    failed = 0
    for name, check in checks:
        if arguments.only and name not in arguments.only:
            continue
        started = time.monotonic()
        try:
            line = f"ok    {check()}"
        except Exception as error:  # noqa: BLE001  (every failure becomes one line)
            failed += 1
            line = f"FAIL  {describe(error)}"
        seconds = time.monotonic() - started
        print(redact(f"{name:<14}{line}  [{seconds:.1f} s]"), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
