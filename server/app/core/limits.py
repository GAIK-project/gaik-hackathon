"""Limits (docs/ARCHITECTURE.md section 15.3): calls, model-backed requests, mail and uploads.

Everything is counted in this process (one replica) and starts from zero after a restart.

Calls (the voice relay):

    if not limits.acquire_call():                       # at most 3 at once
        await ws.send_json({"type": "error", "message": limits.CALLS_BUSY_MESSAGE})
        await ws.close()
        return
    clock = limits.CallClock()                          # 6 minutes, or 60 s without the caller
    try:
        ...
        clock.heard_caller()                            # on every caller turn or speech event
        if reason := clock.ended_by():                  # "duration" | "silence" | None
            await ws.send_json({"type": "error", "message": limits.CALL_END_MESSAGES[reason]})
        await asyncio.wait_for(next_event(), timeout=clock.seconds_left())
    finally:
        limits.release_call()

Model-backed routes (at most 40 per session cookie per 10 minutes, 503 with `HACK_MODELS_OFF=1`):

    @router.post("/api/ai-act/assess", dependencies=[Depends(limits.model_call)])

    limits.count_model_call("mcp")                      # where there is no request (MCP tools)

Mail (at most `MAIL_MAX_PER_HOUR` per hour):

    limits.reserve_mail()                               # right before sending; raises 429

Uploads. `BodyLimitMiddleware` (installed by `main.py`) refuses a request body over the cap of
its path while it streams in, before a handler or a form parser sees it. The per-file rules are
checked by the helpers that store or decode the content:

    stored = await files.save_upload(scope, upload, "audio")      # magic bytes, 25 MB
    limits.check_document_count(len(uploads))                     # 10 per run
    data = limits.decode_base64(content_base64)                   # 15 MB decoded
    limits.check_audio_seconds(seconds)                           # 10 minutes
"""

from __future__ import annotations

import base64
import binascii
import logging
import math
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import BinaryIO, Literal

from fastapi import HTTPException
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import AppError, error_response, status_error
from app.core.settings import get_settings

logger = logging.getLogger(__name__)

MB = 1024 * 1024

MAX_CALLS = 3
CALL_MAX_SECONDS = 6 * 60
CALL_SILENCE_SECONDS = 60

MODEL_CALLS_PER_WINDOW = 40
MODEL_CALL_WINDOW_SECONDS = 10 * 60

MAIL_WINDOW_SECONDS = 60 * 60

AUDIO_MAX_BYTES = 25 * MB
AUDIO_MAX_SECONDS = 10 * 60
DOCUMENT_MAX_BYTES = 15 * MB
DOCUMENTS_MAX_PER_RUN = 10
BASE64_MAX_BYTES = 15 * MB

CALLS_BUSY_MESSAGE = "Kaikki linjat ovat varattuja. Yritä hetken kuluttua uudelleen."
CALL_END_MESSAGES: dict[str, str] = {
    "duration": "Puhelun enimmäiskesto täyttyi.",
    "silence": "Puhelu päättyi, koska linjalla oli hiljaista.",
}

CallEnd = Literal["duration", "silence"]

_lock = threading.Lock()
_now: Callable[[], float] = time.monotonic  # tests replace this to move time


# calls --------------------------------------------------------------------------------------

_active_calls = 0


def acquire_call() -> bool:
    """Take one of the `MAX_CALLS` call slots. False when all are in use."""
    global _active_calls
    with _lock:
        if _active_calls >= MAX_CALLS:
            return False
        _active_calls += 1
        return True


def release_call() -> None:
    """Give a slot back. Call it exactly once per successful `acquire_call`."""
    global _active_calls
    with _lock:
        _active_calls = max(0, _active_calls - 1)


def active_calls() -> int:
    with _lock:
        return _active_calls


@contextmanager
def call_slot() -> Iterator[bool]:
    """`with limits.call_slot() as free:` yields whether a slot was taken and releases it."""
    taken = acquire_call()
    try:
        yield taken
    finally:
        if taken:
            release_call()


class CallClock:
    """The two deadlines of one call: its total length and the silence of the caller."""

    def __init__(
        self,
        *,
        max_seconds: float = CALL_MAX_SECONDS,
        silence_seconds: float = CALL_SILENCE_SECONDS,
    ) -> None:
        self.max_seconds = max_seconds
        self.silence_seconds = silence_seconds
        self.started = _now()
        self.last_caller = self.started

    def heard_caller(self) -> None:
        """The caller spoke or typed: the silence deadline starts again."""
        self.last_caller = _now()

    def elapsed(self) -> float:
        return _now() - self.started

    def ended_by(self) -> CallEnd | None:
        """Which deadline has passed, if any. The total length wins when both have."""
        now = _now()
        if now - self.started >= self.max_seconds:
            return "duration"
        if now - self.last_caller >= self.silence_seconds:
            return "silence"
        return None

    def seconds_left(self) -> float:
        """Seconds until the nearer deadline, never negative: a timeout for the next wait."""
        now = _now()
        total = self.max_seconds - (now - self.started)
        silence = self.silence_seconds - (now - self.last_caller)
        return max(0.0, min(total, silence))


# model-backed requests ----------------------------------------------------------------------

_model_calls: dict[str, deque[float]] = {}


def models_off() -> bool:
    """True when `HACK_MODELS_OFF=1`: every model-backed route answers 503."""
    return get_settings().hack_models_off


def _caller_key(connection: HTTPConnection) -> str:
    """Who a model call is counted for: the session cookie, else the bearer key, else the
    client address (only reached with access control switched off)."""
    state = connection.scope.get("state") or {}
    if state.get("session"):
        return f"session:{state['session']}"
    if state.get("key"):
        return f"key:{state['key']}"
    client = connection.client
    return f"client:{client.host if client else 'unknown'}"


def count_model_call(key: str) -> None:
    """Count one model-backed request for `key`.

    Raises `AppError` 503 `models_off` when models are switched off, and 429
    `too_many_requests` (with `Retry-After`) when `key` has used its 40 requests of the last
    10 minutes. A refused request is not counted.
    """
    if models_off():
        raise AppError("models_off", "Tekoälytoiminnot ovat nyt pois käytöstä.", 503)
    now = _now()
    with _lock:
        _drop_idle_keys(now)
        calls = _model_calls.setdefault(key, deque())
        while calls and now - calls[0] >= MODEL_CALL_WINDOW_SECONDS:
            calls.popleft()
        if len(calls) >= MODEL_CALLS_PER_WINDOW:
            retry_after = max(1, math.ceil(MODEL_CALL_WINDOW_SECONDS - (now - calls[0])))
        else:
            calls.append(now)
            return
    code, _ = status_error(429)
    raise AppError(
        code,
        "Olet tehnyt liian monta pyyntöä. Yritä hetken kuluttua uudelleen.",
        429,
        headers={"Retry-After": str(retry_after)},
    )


def _drop_idle_keys(now: float) -> None:
    if len(_model_calls) < 500:
        return
    for key in [
        key
        for key, calls in _model_calls.items()
        if not calls or now - calls[-1] >= MODEL_CALL_WINDOW_SECONDS
    ]:
        del _model_calls[key]


def model_call(request: HTTPConnection) -> None:
    """The dependency of every model-backed route: `Depends(limits.model_call)`.

    It can also be called with the request inside a handler.
    """
    count_model_call(_caller_key(request))


# mail ---------------------------------------------------------------------------------------

_mail_sent: deque[float] = deque()


def reserve_mail() -> None:
    """Count one outgoing message. Raises `AppError` 429 `mail_limit` when
    `MAIL_MAX_PER_HOUR` messages were already counted in the last hour.

    Call it right before sending; a send that fails afterwards still counts.
    """
    limit = get_settings().mail_max_per_hour
    now = _now()
    with _lock:
        while _mail_sent and now - _mail_sent[0] >= MAIL_WINDOW_SECONDS:
            _mail_sent.popleft()
        if len(_mail_sent) < limit:
            _mail_sent.append(now)
            return
    raise AppError(
        "mail_limit",
        "Sähköposteja on lähetetty tunnin aikana liian monta. Yritä myöhemmin uudelleen.",
        429,
    )


def mails_last_hour() -> int:
    now = _now()
    with _lock:
        return sum(1 for sent in _mail_sent if now - sent < MAIL_WINDOW_SECONDS)


# uploads ------------------------------------------------------------------------------------

DEFAULT_BODY_BYTES = 1 * MB
# Room for the other form fields, the multipart boundaries and JSON around base64 content.
BODY_OVERHEAD_BYTES = 1 * MB
_BASE64_BODY_BYTES = math.ceil(BASE64_MAX_BYTES / 3) * 4 + BODY_OVERHEAD_BYTES

_DEFAULT_BODY_LIMITS: tuple[tuple[str, int], ...] = (
    (r"/api/intake/audio", AUDIO_MAX_BYTES + BODY_OVERHEAD_BYTES),
    (r"/api/reports/[^/]+/correct", AUDIO_MAX_BYTES + BODY_OVERHEAD_BYTES),
    (r"/api/studio/run", DOCUMENT_MAX_BYTES * DOCUMENTS_MAX_PER_RUN + BODY_OVERHEAD_BYTES),
    (r"/mcp/?", _BASE64_BODY_BYTES),
    (r"/api/mcp/try", _BASE64_BODY_BYTES),
)
_body_limits: list[tuple[re.Pattern[str], int]] = []


def set_body_limit(path_pattern: str, max_bytes: int) -> None:
    """Set the largest request body for the paths that fully match a regular expression.

    The newest rule for a path wins; a path without a rule gets `DEFAULT_BODY_BYTES` (1 MB).
    """
    _body_limits.insert(0, (re.compile(path_pattern), max_bytes))


def body_limit_for(path: str) -> int:
    for pattern, max_bytes in _body_limits:
        if pattern.fullmatch(path):
            return max_bytes
    return DEFAULT_BODY_BYTES


def too_large(message: str | None = None) -> AppError:
    code, default = status_error(413)
    return AppError(code, message or default, 413)


class _BodyTooLarge(HTTPException):
    """Raised out of `receive` when a body passes its cap. FastAPI re-raises an
    `HTTPException` met while it parses a body, so the usual 413 envelope goes out."""

    def __init__(self) -> None:
        # The rest of the body is not wanted: have the server close the connection.
        super().__init__(status_code=413, headers={"Connection": "close"})


class BodyLimitMiddleware:
    """Refuse a request body over the cap of its path while it streams in.

    A declared `Content-Length` over the cap is refused before the app runs. A body without
    one (chunked) is counted as it arrives: the read that passes the cap raises, nothing more
    is read, and the answer is the 413 envelope. Pure ASGI, so streams pass untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = body_limit_for(scope["path"])
        declared = _content_length(scope)
        if declared is not None and declared > limit:
            logger.info("%s %s refused: body over its limit", scope["method"], scope["path"])
            await self._refuse(scope, receive, send)
            return

        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    logger.info(
                        "%s %s stopped: body over its limit", scope["method"], scope["path"]
                    )
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            # Only reached when the app did not turn the error into a response itself.
            if started:
                raise
            await self._refuse(scope, receive, send)

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send) -> None:
        code, message = status_error(413)
        response = error_response(code, message, 413, headers={"Connection": "close"})
        await response(scope, receive, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers") or ():
        if name == b"content-length":
            text = value.decode("latin-1").strip()
            return int(text) if text.isdigit() else None
    return None


def check_size(size: int, max_bytes: int) -> None:
    """Raise the 413 error when `size` is over `max_bytes`."""
    if size > max_bytes:
        raise too_large("Tiedosto on liian suuri.")


def copy_capped(
    source: BinaryIO, target: BinaryIO, max_bytes: int, *, chunk_size: int = 256 * 1024
) -> int:
    """Copy `source` to `target` chunk by chunk and stop with the 413 error as soon as more
    than `max_bytes` have been read. Returns the number of bytes written. Blocking: call it
    in a worker thread."""
    written = 0
    while chunk := source.read(chunk_size):
        written += len(chunk)
        if written > max_bytes:
            raise too_large("Tiedosto on liian suuri.")
        target.write(chunk)
    return written


def check_document_count(count: int) -> None:
    """At most `DOCUMENTS_MAX_PER_RUN` documents in one run."""
    if count > DOCUMENTS_MAX_PER_RUN:
        raise AppError(
            "too_many_files",
            f"Voit lähettää enintään {DOCUMENTS_MAX_PER_RUN} tiedostoa kerralla.",
            413,
        )


def check_audio_seconds(seconds: float) -> None:
    """At most `AUDIO_MAX_SECONDS` of audio."""
    if seconds > AUDIO_MAX_SECONDS:
        raise AppError(
            "audio_too_long",
            f"Äänite on liian pitkä. Enimmäispituus on {AUDIO_MAX_SECONDS // 60} minuuttia.",
            413,
        )


def decode_base64(content_base64: str, *, max_bytes: int = BASE64_MAX_BYTES) -> bytes:
    """Decode `content_base64` (the MCP tools' file argument). The encoded length is checked
    first, so an oversized value is refused without being decoded."""
    if not isinstance(content_base64, str):
        raise AppError("invalid_base64", "Tiedoston sisältö ei ole kelvollista base64-muotoa.", 400)
    if len(content_base64) > math.ceil(max_bytes / 3) * 4 + 1024:
        raise too_large("Tiedosto on liian suuri.")
    try:
        data = base64.b64decode("".join(content_base64.split()), validate=True)
    except (binascii.Error, ValueError):
        raise AppError(
            "invalid_base64", "Tiedoston sisältö ei ole kelvollista base64-muotoa.", 400
        ) from None
    check_size(len(data), max_bytes)
    return data


# tests --------------------------------------------------------------------------------------


def reset() -> None:
    """Forget every count and restore the body limits. For tests."""
    global _active_calls
    with _lock:
        _active_calls = 0
        _model_calls.clear()
        _mail_sent.clear()
        _body_limits[:] = [(re.compile(pattern), size) for pattern, size in _DEFAULT_BODY_LIMITS]


reset()
