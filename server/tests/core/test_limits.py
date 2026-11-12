from __future__ import annotations

import base64
import io

import pytest
from fastapi import Depends, FastAPI, Request, UploadFile
from fastapi.testclient import TestClient

from app.core import limits
from app.core.auth import cookie_name, install_auth, sign_session
from app.core.errors import AppError, install_error_handlers
from app.core.settings import get_settings

BASE_URL = "https://testserver"
TEST_ORIGIN = "https://gaik-hackathon.example"  # PUBLIC_BASE_URL of the test settings


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(limits, "_now", fake)
    return fake


def browser(settings) -> dict[str, str]:
    """Headers of one signed-in browser; every call is a new session cookie."""
    value = sign_session(settings.hack_session_secret)
    return {"Cookie": f"{cookie_name(settings)}={value}", "Origin": TEST_ORIGIN}


# calls --------------------------------------------------------------------------------------


def test_at_most_three_calls_at_once():
    assert limits.MAX_CALLS == 3
    assert [limits.acquire_call() for _ in range(3)] == [True, True, True]
    assert limits.acquire_call() is False  # the fourth caller is told the lines are busy
    assert limits.active_calls() == 3
    assert limits.CALLS_BUSY_MESSAGE.endswith(".")

    limits.release_call()
    assert limits.acquire_call() is True
    assert limits.acquire_call() is False


def test_releasing_never_goes_below_zero():
    limits.release_call()
    limits.release_call()
    assert limits.active_calls() == 0
    assert limits.acquire_call() is True


def test_call_slot_releases_also_when_the_call_fails():
    with pytest.raises(RuntimeError):
        with limits.call_slot() as free:
            assert free is True
            assert limits.active_calls() == 1
            raise RuntimeError("relay failed")
    assert limits.active_calls() == 0

    for _ in range(3):
        limits.acquire_call()
    with limits.call_slot() as free:
        assert free is False
    assert limits.active_calls() == 3  # a refused caller gives nothing back


def test_a_call_ends_after_six_minutes(clock):
    call = limits.CallClock()
    assert limits.CALL_MAX_SECONDS == 360
    for _ in range(35):  # the caller keeps talking
        clock.advance(10)
        call.heard_caller()
        assert call.ended_by() is None
    assert call.elapsed() == 350
    assert call.seconds_left() == 10
    clock.advance(10)
    call.heard_caller()
    assert call.ended_by() == "duration"
    assert call.seconds_left() == 0
    assert limits.CALL_END_MESSAGES["duration"]


def test_a_call_ends_after_sixty_seconds_without_the_caller(clock):
    call = limits.CallClock()
    assert limits.CALL_SILENCE_SECONDS == 60
    clock.advance(59)
    assert call.ended_by() is None
    assert call.seconds_left() == 1
    call.heard_caller()
    clock.advance(59)
    assert call.ended_by() is None
    clock.advance(1)
    assert call.ended_by() == "silence"
    assert limits.CALL_END_MESSAGES["silence"]


# model-backed requests ----------------------------------------------------------------------


def model_app() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/api/ai-act/assess", dependencies=[Depends(limits.model_call)])
    async def assess() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/intake/text")
    async def intake(request: Request) -> dict[str, bool]:
        limits.model_call(request)  # the same check, called inside a handler
        return {"ok": True}

    @app.get("/api/reports")
    async def reports() -> dict[str, bool]:
        return {"ok": True}

    install_auth(app, get_settings())
    return TestClient(app, base_url=BASE_URL)


def test_forty_model_calls_per_session_cookie_then_429(clock, settings):
    client = model_app()
    first, second = browser(settings), browser(settings)
    assert (limits.MODEL_CALLS_PER_WINDOW, limits.MODEL_CALL_WINDOW_SECONDS) == (40, 600)

    for _ in range(40):
        assert client.post("/api/ai-act/assess", headers=first).status_code == 200
        clock.advance(1)

    refused = client.post("/api/ai-act/assess", headers=first)
    assert refused.status_code == 429
    assert refused.json() == {
        "error": {
            "code": "too_many_requests",
            "message": "Olet tehnyt liian monta pyyntöä. Yritä hetken kuluttua uudelleen.",
        }
    }
    assert refused.headers["retry-after"] == "560"  # the oldest call leaves the window then
    assert client.post("/api/intake/text", headers=first).status_code == 429  # one count

    # Another browser has its own count, and routes without a model are not limited.
    assert client.post("/api/ai-act/assess", headers=second).status_code == 200
    assert client.get("/api/reports", headers=first).status_code == 200

    clock.advance(559)
    assert client.post("/api/ai-act/assess", headers=first).status_code == 429
    clock.advance(1)
    assert client.post("/api/ai-act/assess", headers=first).status_code == 200
    assert client.post("/api/ai-act/assess", headers=first).status_code == 429


def test_a_refused_model_call_is_not_counted(clock):
    for _ in range(40):
        limits.count_model_call("session:a")
    for _ in range(100):
        with pytest.raises(AppError):
            limits.count_model_call("session:a")
    clock.advance(600)
    limits.count_model_call("session:a")  # the refusals did not extend the wait


def test_the_bridge_key_is_one_caller(clock, bridge_auth):
    client = model_app()
    for _ in range(40):
        assert client.post("/api/intake/text", headers=bridge_auth).status_code == 200
    assert client.post("/api/intake/text", headers=bridge_auth).status_code == 429


def test_models_off_answers_503_and_counts_nothing(clock, settings, monkeypatch):
    client = model_app()
    headers = browser(settings)
    monkeypatch.setenv("HACK_MODELS_OFF", "1")
    get_settings.cache_clear()
    assert limits.models_off() is True

    for _ in range(45):
        response = client.post("/api/ai-act/assess", headers=headers)
        assert response.status_code == 503
    assert response.json() == {
        "error": {"code": "models_off", "message": "Tekoälytoiminnot ovat nyt pois käytöstä."}
    }
    assert client.get("/api/reports", headers=headers).status_code == 200
    with pytest.raises(AppError) as off:
        limits.count_model_call("mcp")
    assert (off.value.status, off.value.code) == (503, "models_off")

    monkeypatch.setenv("HACK_MODELS_OFF", "0")
    get_settings.cache_clear()
    assert client.post("/api/ai-act/assess", headers=headers).status_code == 200


def test_idle_callers_are_forgotten(clock):
    for index in range(600):
        limits.count_model_call(f"session:{index}")
    clock.advance(601)
    limits.count_model_call("session:new")
    assert len(limits._model_calls) == 1


# mail ---------------------------------------------------------------------------------------


def test_at_most_ten_mails_an_hour(clock):
    assert get_settings().mail_max_per_hour == 10
    for _ in range(10):
        limits.reserve_mail()
        clock.advance(60)
    with pytest.raises(AppError) as refused:
        limits.reserve_mail()
    assert (refused.value.status, refused.value.code) == (429, "mail_limit")
    assert "Sähköposteja" in refused.value.message
    assert limits.mails_last_hour() == 10

    clock.advance(3000)  # an hour after the first one
    limits.reserve_mail()
    with pytest.raises(AppError):
        limits.reserve_mail()


def test_mail_limit_comes_from_the_setting(clock, monkeypatch):
    monkeypatch.setenv("MAIL_MAX_PER_HOUR", "2")
    get_settings.cache_clear()
    limits.reserve_mail()
    limits.reserve_mail()
    with pytest.raises(AppError):
        limits.reserve_mail()

    monkeypatch.setenv("MAIL_MAX_PER_HOUR", "0")
    get_settings.cache_clear()
    limits.reset()
    with pytest.raises(AppError):
        limits.reserve_mail()


def test_reset_clears_every_count(clock):
    limits.acquire_call()
    limits.reserve_mail()
    limits.count_model_call("session:a")
    limits.set_body_limit(r"/api/x", 5)
    limits.reset()
    assert limits.active_calls() == 0
    assert limits.mails_last_hour() == 0
    assert limits._model_calls == {}
    assert limits.body_limit_for("/api/x") == limits.DEFAULT_BODY_BYTES


# upload rules -------------------------------------------------------------------------------


def test_the_numbers_of_the_contract():
    assert limits.AUDIO_MAX_BYTES == 25 * 1024 * 1024
    assert limits.AUDIO_MAX_SECONDS == 600
    assert limits.DOCUMENT_MAX_BYTES == 15 * 1024 * 1024
    assert limits.DOCUMENTS_MAX_PER_RUN == 10
    assert limits.BASE64_MAX_BYTES == 15 * 1024 * 1024


def test_document_count_and_audio_length():
    limits.check_document_count(10)
    with pytest.raises(AppError) as many:
        limits.check_document_count(11)
    assert (many.value.status, many.value.code) == (413, "too_many_files")

    limits.check_audio_seconds(600)
    with pytest.raises(AppError) as long:
        limits.check_audio_seconds(600.5)
    assert (long.value.status, long.value.code) == (413, "audio_too_long")
    assert "10 minuuttia" in long.value.message


def test_copy_capped_stops_reading_at_the_cap():
    class Source(io.BytesIO):
        reads = 0

        def read(self, size: int = -1) -> bytes:
            self.reads += 1
            return super().read(size)

    source, target = Source(b"x" * 10_000), io.BytesIO()
    with pytest.raises(AppError) as raised:
        limits.copy_capped(source, target, 2_500, chunk_size=1_000)
    assert raised.value.status == 413
    assert source.reads == 3  # the third chunk passed the cap; the rest was never read
    assert len(target.getvalue()) == 2_000

    target = io.BytesIO()
    assert limits.copy_capped(io.BytesIO(b"x" * 2_500), target, 2_500, chunk_size=1_000) == 2_500
    assert len(target.getvalue()) == 2_500


def test_decode_base64():
    assert limits.decode_base64(base64.b64encode(b"hei").decode()) == b"hei"
    assert limits.decode_base64("aGVp\nIG1h\r\nYWlsbWE=") == b"hei maailma"  # wrapped lines

    for bad in ("not base64!", "aGV", "a", "<html>", None, 12):
        with pytest.raises(AppError) as invalid:
            limits.decode_base64(bad)  # type: ignore[arg-type]
        assert (invalid.value.status, invalid.value.code) == (400, "invalid_base64")

    at_cap = base64.b64encode(b"x" * 3_000).decode()
    assert len(limits.decode_base64(at_cap, max_bytes=3_000)) == 3_000
    with pytest.raises(AppError) as large:
        limits.decode_base64(base64.b64encode(b"x" * 3_001).decode(), max_bytes=3_000)
    assert large.value.status == 413


def test_oversized_base64_is_refused_before_it_is_decoded(monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("decoded an oversized value")

    monkeypatch.setattr(limits.base64, "b64decode", never)
    with pytest.raises(AppError) as large:
        limits.decode_base64("A" * 200_000, max_bytes=3_000)
    assert large.value.status == 413


# request bodies -----------------------------------------------------------------------------

MB = 1024 * 1024


@pytest.mark.parametrize(
    ("path", "megabytes"),
    [
        ("/api/intake/audio", 26),
        ("/api/reports/rep_1/correct", 26),
        ("/api/studio/run", 151),
        ("/mcp", 21),
        ("/mcp/", 21),
        ("/api/mcp/try", 21),
        ("/api/intake/text", 1),
        ("/api/reports/rep_1/fields", 1),
        ("/api/studio/generate", 1),
        ("/api/ai-act/assess", 1),
        ("/api/whatsapp/webhook", 1),
        ("/api/intake/audio/extra", 1),
        ("/", 1),
    ],
)
def test_body_limit_of_each_path(path, megabytes):
    assert limits.body_limit_for(path) == megabytes * MB


def test_a_feature_can_set_its_own_body_limit():
    limits.set_body_limit(r"/api/studio/samples/[^/]+", 3 * MB)
    assert limits.body_limit_for("/api/studio/samples/s_1") == 3 * MB
    assert limits.body_limit_for("/api/studio/samples") == limits.DEFAULT_BODY_BYTES


def upload_app(seen: list[str]) -> FastAPI:
    """A route that takes an upload the usual FastAPI way, behind the body limit."""
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/api/intake/audio")
    async def intake(file: UploadFile) -> dict[str, int]:
        seen.append("handler")
        return {"size": len(await file.read())}

    @app.post("/api/intake/text")
    async def text(request: Request) -> dict[str, int]:
        seen.append("handler")
        return {"size": len(await request.body())}

    app.add_middleware(limits.BodyLimitMiddleware)
    return app


def multipart(size: int, boundary: str = "testboundary") -> tuple[bytes, str]:
    head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="aani.ogg"\r\n'
        "Content-Type: audio/ogg\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + b"OggS" + b"\0" * (size - 4) + tail, f"multipart/form-data; boundary={boundary}"


async def send_in_chunks(
    app: FastAPI,
    path: str,
    body: bytes,
    content_type: str,
    *,
    chunk_size: int,
    declare_length: bool,
) -> tuple[int, dict[str, str], bytes, int]:
    """Call the app the way a server does while a body arrives piece by piece.

    Returns the status, the headers, the response body and how many pieces the app took.
    """
    headers = [(b"content-type", content_type.encode()), (b"host", b"testserver")]
    if declare_length:
        headers.append((b"content-length", str(len(body)).encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 443),
    }
    chunks = [body[index : index + chunk_size] for index in range(0, len(body), chunk_size)]
    taken = 0
    sent: list[dict] = []

    async def receive() -> dict:
        nonlocal taken
        if taken >= len(chunks):
            return {"type": "http.disconnect"}
        chunk = chunks[taken]
        taken += 1
        return {"type": "http.request", "body": chunk, "more_body": taken < len(chunks)}

    async def send(message: dict) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    response_headers = {key.decode(): value.decode() for key, value in start["headers"]}
    response_body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return start["status"], response_headers, response_body, taken


async def test_upload_over_the_limit_is_refused_while_it_streams_in():
    seen: list[str] = []
    app = upload_app(seen)
    body, content_type = multipart(40 * MB)  # audio may be 25 MB
    pieces = len(body) // MB + 1

    status, headers, response, taken = await send_in_chunks(
        app, "/api/intake/audio", body, content_type, chunk_size=MB, declare_length=False
    )

    assert status == 413
    assert b'"code":"too_large"' in response
    assert "Lähetys on liian suuri." in response.decode()
    assert headers["connection"] == "close"
    assert seen == []  # the handler never ran
    # Reading stopped at the piece that passed 26 MB: the last 14 MB were never taken.
    assert taken == 27
    assert taken < pieces


async def test_upload_with_a_declared_length_over_the_limit_is_refused_unread():
    seen: list[str] = []
    body, content_type = multipart(40 * MB)
    status, headers, response, taken = await send_in_chunks(
        upload_app(seen),
        "/api/intake/audio",
        body,
        content_type,
        chunk_size=MB,
        declare_length=True,
    )
    assert status == 413
    assert b'"code":"too_large"' in response
    assert headers["connection"] == "close"
    assert taken == 0  # not one byte of the body was read
    assert seen == []


async def test_upload_inside_the_limit_passes_in_pieces():
    seen: list[str] = []
    body, content_type = multipart(25 * MB)  # exactly the largest audio file
    for declare_length in (True, False):
        status, _, response, taken = await send_in_chunks(
            upload_app(seen),
            "/api/intake/audio",
            body,
            content_type,
            chunk_size=MB,
            declare_length=declare_length,
        )
        assert status == 200
        assert response == b'{"size":%d}' % (25 * MB)
        assert taken == len(body) // MB + 1
    assert seen == ["handler", "handler"]


async def test_json_bodies_are_capped_at_one_megabyte():
    seen: list[str] = []
    app = upload_app(seen)
    small = b'{"text":"' + b"a" * 1000 + b'"}'
    status, _, _, _ = await send_in_chunks(
        app, "/api/intake/text", small, "application/json", chunk_size=256, declare_length=False
    )
    assert status == 200
    assert seen == ["handler"]

    seen.clear()
    large = b'{"text":"' + b"a" * (2 * MB) + b'"}'
    status, _, response, taken = await send_in_chunks(
        app,
        "/api/intake/text",
        large,
        "application/json",
        chunk_size=64 * 1024,
        declare_length=False,
    )
    assert status == 413
    assert b"too_large" in response
    assert taken == 17  # 16 pieces of 64 KB are 1 MB; the 17th passed it
    assert seen == ["handler"]  # the handler started reading and was stopped


async def test_a_raw_asgi_app_behind_the_limit_gets_the_413_too():
    """An app that does not turn the error into a response itself (a mounted ASGI app)."""
    reached_end = False

    async def raw_app(scope, receive, send):
        nonlocal reached_end
        more = True
        while more:
            message = await receive()
            more = message.get("more_body", False)
        reached_end = True

    guarded = limits.BodyLimitMiddleware(raw_app)
    status, _, response, taken = await send_in_chunks(
        guarded, "/mcp", b"x" * (30 * MB), "application/json", chunk_size=MB, declare_length=False
    )
    assert status == 413
    assert b"too_large" in response
    assert taken == 22
    assert reached_end is False


def test_the_real_app_limits_bodies_behind_the_login(client, anon_client):
    # Unknown paths: a small body reaches the router (404), a large one does not (413).
    assert client.post("/api/ei-ole", content=b"x" * 1024).status_code == 404
    large = client.post("/api/ei-ole", content=b"x" * (MB + 1))
    assert large.status_code == 413
    assert large.json() == {"error": {"code": "too_large", "message": "Lähetys on liian suuri."}}
    # Access control comes first: without a login the size is not even looked at.
    assert anon_client.post("/api/ei-ole", content=b"x" * (MB + 1)).status_code == 401
    # The open webhook is capped as well.
    assert anon_client.post("/api/whatsapp/webhook", content=b"x" * (MB + 1)).status_code == 413
