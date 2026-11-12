from __future__ import annotations

import base64
import time

import pytest
from fastapi import Depends, FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.auth import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    HOST_COOKIE_NAME,
    auth_kind,
    cookie_name,
    install_auth,
    key_allowed,
    key_scope,
    read_session,
    require_bearer,
    require_key,
    sign_session,
    verify_session,
)
from app.core.errors import install_error_handlers
from app.core.settings import Settings, get_settings

BASE_URL = "https://testserver"
TEST_ORIGIN = "https://gaik-hackathon.example"  # PUBLIC_BASE_URL of the test settings
CHALLENGE = 'Basic realm="GAIK mini-hackathon"'
ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


def make_client(settings: Settings | None = None) -> TestClient:
    """A small app behind the real access rules. Every path answers with how it was reached."""
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/api/whatsapp/session", dependencies=[Depends(require_key("bridge"))])
    async def bridge_only() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/whatsapp/pending", dependencies=[Depends(require_bearer)])
    async def bridge_only_old_name() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/mcp/only", dependencies=[Depends(require_key("mcp"))])
    async def mcp_only() -> dict[str, bool]:
        return {"ok": True}

    @app.api_route("/{rest:path}", methods=ALL_METHODS)
    async def anything(request: Request) -> dict[str, object]:
        return {
            "auth": auth_kind(request),
            "key": key_scope(request),
            "session": bool(request.state.session),
        }

    @app.websocket("/api/call/ws")
    async def call(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({"auth": websocket.state.auth, "key": websocket.state.key})
        await websocket.close()

    @app.websocket("/api/other/ws")
    async def other(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({"auth": websocket.state.auth})
        await websocket.close()

    install_auth(app, settings or get_settings())
    return TestClient(app, base_url=BASE_URL)


def session_cookie(client: TestClient, basic_auth: dict[str, str]) -> str:
    """Log in with Basic and return the `Cookie` header value of the session."""
    response = client.get("/", headers=basic_auth)
    assert response.status_code == 200
    name = cookie_name(get_settings())
    value = response.cookies.get(name)
    assert value
    client.cookies.clear()  # tests pass the cookie explicitly
    return f"{name}={value}"


def ws_close_code(client: TestClient, path: str, headers: dict[str, str] | None = None) -> int:
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(path, headers=dict(headers or {})) as websocket:
            websocket.receive_json()
    return closed.value.code


def local_settings(monkeypatch, *, dev: bool = False) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8787")
    monkeypatch.setenv("HACK_DEV", "1" if dev else "0")
    get_settings.cache_clear()


# open paths ---------------------------------------------------------------------------------


def test_open_paths_need_nothing():
    client = make_client()
    assert client.get("/api/health").json()["auth"] == "open"
    assert client.get("/api/whatsapp/webhook").json()["auth"] == "open"
    assert client.post("/api/whatsapp/webhook", content=b"{}").json()["auth"] == "open"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("DELETE", "/api/whatsapp/webhook"),
        ("PUT", "/api/whatsapp/webhook"),
        ("POST", "/api/health"),
        ("GET", "/api/health/"),
        ("GET", "/api/whatsapp/webhook/x"),
        ("GET", "/api/whatsapp/status"),
        ("GET", "/api/whatsapp/pending"),
    ],
)
def test_nothing_else_is_open(method, path):
    assert make_client().request(method, path).status_code == 401


@pytest.mark.parametrize("path", ["/", "/api/whoami"])
def test_no_credentials_gets_401_with_the_basic_challenge(path):
    response = make_client().get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == CHALLENGE
    assert response.json() == {"error": {"code": "unauthorized", "message": "Kirjaudu sisään."}}
    assert "set-cookie" not in response.headers


# Basic credentials and the cookie -----------------------------------------------------------


def test_wrong_basic_credentials_are_refused():
    client = make_client()
    for user, password in (("test-user", "wrong"), ("wrong", "test-password"), ("", "")):
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        response = client.get("/", headers={"Authorization": f"Basic {token}"})
        assert response.status_code == 401
        assert "set-cookie" not in response.headers
    assert client.get("/", headers={"Authorization": "Basic not-base64!"}).status_code == 401


def test_basic_credentials_pass_on_get_and_set_the_host_cookie(basic_auth):
    response = make_client().get("/", headers=basic_auth)
    assert response.status_code == 200
    assert response.json() == {"auth": "basic", "key": None, "session": True}
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{HOST_COOKIE_NAME}=")
    attributes = {part.strip().lower() for part in cookie.split(";")[1:]}
    assert attributes == {
        "path=/",
        f"max-age={COOKIE_MAX_AGE}",
        "httponly",
        "samesite=lax",
        "secure",
    }
    assert "domain" not in cookie.lower()  # a __Host- cookie has none
    assert COOKIE_MAX_AGE == 7 * 24 * 60 * 60


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "HEAD"])
def test_basic_credentials_are_honoured_only_on_get(method, basic_auth):
    headers = {**basic_auth, "Origin": TEST_ORIGIN}
    response = make_client().request(method, "/api/whoami", headers=headers)
    assert response.status_code == 401
    # No Basic challenge where Basic cannot work: the browser would ask and retry for ever.
    assert "www-authenticate" not in response.headers
    assert "set-cookie" not in response.headers


def test_cookie_is_plain_gh_session_in_local_development(basic_auth, monkeypatch):
    local_settings(monkeypatch)
    cookie = make_client().get("/", headers=basic_auth).headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}=")
    assert "secure" not in cookie.lower()


def test_cookie_name_follows_the_public_address(monkeypatch):
    assert cookie_name(get_settings()) == "__Host-gh_session"
    for local in ("http://localhost:8787", "http://127.0.0.1:8787", "http://localhost"):
        monkeypatch.setenv("PUBLIC_BASE_URL", local)
        assert cookie_name(Settings()) == "gh_session"
    for public in ("https://localhost:8787", "http://localhost.example.com", "https://x.example"):
        monkeypatch.setenv("PUBLIC_BASE_URL", public)
        assert cookie_name(Settings()) == "__Host-gh_session"


def test_session_cookie_is_accepted(basic_auth):
    client = make_client()
    cookie = session_cookie(client, basic_auth)
    for path in ("/", "/api/whoami"):
        response = client.get(path, headers={"Cookie": cookie})
        assert response.status_code == 200
        assert response.json() == {"auth": "cookie", "key": None, "session": True}
        assert "set-cookie" not in response.headers


def test_cookie_is_found_among_other_cookies(basic_auth):
    client = make_client()
    cookie = session_cookie(client, basic_auth)
    # A cookie `http.cookies.SimpleCookie` cannot parse must not hide ours.
    header = f'other=a b; bad[key]=1; {cookie}; last="x"'
    assert client.get("/", headers={"Cookie": header}).json()["auth"] == "cookie"


def test_only_the_cookie_name_of_this_deployment_counts(basic_auth, settings):
    value = sign_session(settings.hack_session_secret)
    client = make_client()
    assert client.get("/", headers={"Cookie": f"{HOST_COOKIE_NAME}={value}"}).status_code == 200
    assert client.get("/", headers={"Cookie": f"{COOKIE_NAME}={value}"}).status_code == 401


def test_browser_flow_keeps_the_cookie(basic_auth):
    client = make_client()
    assert client.get("/", headers=basic_auth).status_code == 200
    assert client.get("/").json()["auth"] == "cookie"  # the jar sends it back


def test_tampered_or_expired_cookie_is_refused(basic_auth, settings):
    client = make_client()
    name, _, cookie = session_cookie(client, basic_auth).partition("=")
    tampered = cookie[:-1] + ("0" if cookie[-1] != "0" else "1")
    expired = sign_session(settings.hack_session_secret, now=time.time() - COOKIE_MAX_AGE - 5)
    forged = sign_session("another-secret-of-at-least-32-characters")
    for value in (tampered, expired, forged, "x", ""):
        response = client.get("/", headers={"Cookie": f"{name}={value}"})
        assert response.status_code == 401, value


def test_sign_and_verify(settings):
    secret = settings.hack_session_secret
    value = sign_session(secret)
    assert verify_session(secret, value)
    assert not verify_session(secret, value, now=time.time() + COOKIE_MAX_AGE + 1)
    assert not verify_session("", value)
    assert not verify_session(secret, None)
    # Each cookie has its own session id; the model-call limit is counted per id.
    first, second = read_session(secret, value), read_session(secret, sign_session(secret))
    assert first and second and first != second


# Origin -------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_write_with_cookie_needs_the_apps_own_origin(method, basic_auth):
    client = make_client()
    cookie = {"Cookie": session_cookie(client, basic_auth)}

    allowed = client.request(method, "/api/whoami", headers={**cookie, "Origin": TEST_ORIGIN})
    assert allowed.status_code == 200
    assert allowed.json()["auth"] == "cookie"

    for origin in (
        None,
        "https://evil.example",
        "null",
        "http://gaik-hackathon.example",  # another scheme
        "https://gaik-hackathon.example:8443",  # another port
        "https://gaik-hackathon.example.evil.example",
        "https://sub.gaik-hackathon.example",
        "http://localhost:3000",  # the dev front end, and this is not dev
    ):
        headers = {**cookie, **({"Origin": origin} if origin else {})}
        refused = client.request(method, "/api/whoami", headers=headers)
        assert refused.status_code == 403, origin
        assert refused.json() == {
            "error": {"code": "bad_origin", "message": "Pyyntö tuli vieraalta sivustolta."}
        }


def test_read_with_cookie_needs_no_origin(basic_auth):
    client = make_client()
    cookie = {"Cookie": session_cookie(client, basic_auth)}
    assert client.get("/api/whoami", headers=cookie).status_code == 200
    foreign = {**cookie, "Origin": "https://evil.example"}
    assert client.get("/api/whoami", headers=foreign).status_code == 200
    assert client.head("/api/whoami", headers=cookie).status_code != 403


def test_origin_is_compared_as_an_origin(basic_auth, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://Gaik-Hackathon.example:443/some/path/")
    get_settings.cache_clear()
    client = make_client()
    cookie = {"Cookie": session_cookie(client, basic_auth)}
    response = client.post("/api/whoami", headers={**cookie, "Origin": TEST_ORIGIN})
    assert response.status_code == 200


def test_dev_front_end_origin_is_allowed_only_in_dev(basic_auth, monkeypatch):
    dev_origin = {"Origin": "http://localhost:3000"}

    local_settings(monkeypatch, dev=True)
    client = make_client()
    cookie = {"Cookie": session_cookie(client, basic_auth)}
    assert client.post("/api/whoami", headers={**cookie, **dev_origin}).status_code == 200
    own = {**cookie, "Origin": "http://localhost:8787"}
    assert client.post("/api/whoami", headers=own).status_code == 200
    with client.websocket_connect("/api/call/ws", headers={**cookie, **dev_origin}) as websocket:
        assert websocket.receive_json()["auth"] == "cookie"

    local_settings(monkeypatch, dev=False)
    client = make_client()
    cookie = {"Cookie": session_cookie(client, basic_auth)}
    assert client.post("/api/whoami", headers={**cookie, **dev_origin}).status_code == 403
    assert ws_close_code(client, "/api/call/ws", {**cookie, **dev_origin}) == 4403


def test_bearer_requests_need_no_origin(bridge_auth, mcp_auth):
    client = make_client()
    assert client.post("/api/intake/text", headers=bridge_auth).status_code == 200
    foreign = {**bridge_auth, "Origin": "https://evil.example"}
    assert client.post("/api/intake/text", headers=foreign).status_code == 200
    assert client.post("/mcp", headers=mcp_auth).status_code == 200


# scoped keys --------------------------------------------------------------------------------

BRIDGE_ROUTES = [
    ("POST", "/api/whatsapp/session"),
    ("GET", "/api/whatsapp/pending"),
    ("POST", "/api/intake/audio"),
    ("POST", "/api/intake/text"),
    ("POST", "/api/reports/rep_1/approve"),
    ("POST", "/api/reports/rep_1/correct"),
    ("GET", "/api/reports/rep_1"),
    ("GET", "/api/reports/rep_1/files/f_1.docx"),
    ("GET", "/api/files/reports/rep_1/f_1.docx"),
]

NOT_BRIDGE_ROUTES = [
    ("GET", "/api/reports"),
    ("GET", "/api/reports/stream"),
    ("GET", "/api/reports/rep_1/stream"),
    ("PATCH", "/api/reports/rep_1/fields"),
    ("DELETE", "/api/reports/rep_1"),
    ("POST", "/api/reports/rep_1"),
    ("GET", "/api/reports/rep_1/approve"),
    ("PUT", "/api/reports/rep_1/approve"),
    ("POST", "/api/reports/rep_1/approve/extra"),
    ("POST", "/api/reports/rep_1/files/f_1.docx"),
    ("DELETE", "/api/files/reports/rep_1/f_1.docx"),
    ("GET", "/api/files"),
    ("GET", "/api/intake"),
    ("GET", "/api/whatsapp/status"),
    ("GET", "/api/report-types"),
    ("POST", "/api/report-types"),
    ("GET", "/api/mail/recipients"),
    ("POST", "/api/studio/generate"),
    ("POST", "/api/ai-act/assess"),
    ("GET", "/api/mcp/info"),
    ("POST", "/api/mcp/try"),
    ("GET", "/api/whoami"),
    ("POST", "/mcp"),
    ("GET", "/"),
    ("GET", "/soita/"),
]

MCP_ROUTES = [("POST", "/mcp"), ("GET", "/mcp"), ("DELETE", "/mcp"), ("POST", "/mcp/")]

NOT_MCP_ROUTES = [
    *BRIDGE_ROUTES,
    ("GET", "/api/reports"),
    ("POST", "/api/mcp/try"),
    ("GET", "/api/mcp/info"),
    ("GET", "/api/mcp/activity"),
    ("GET", "/mcpx"),
    ("GET", "/"),
]


@pytest.mark.parametrize(("method", "path"), BRIDGE_ROUTES)
def test_bridge_key_is_accepted_on_its_routes(method, path, bridge_auth):
    response = make_client().request(method, path, headers=bridge_auth)
    assert response.status_code == 200, response.text
    if "auth" in response.json():
        assert response.json() == {"auth": "bearer", "key": "bridge", "session": False}


@pytest.mark.parametrize(("method", "path"), NOT_BRIDGE_ROUTES)
def test_bridge_key_on_any_other_route_is_a_401(method, path, bridge_auth):
    response = make_client().request(method, path, headers=bridge_auth)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(("method", "path"), MCP_ROUTES)
def test_mcp_key_is_accepted_on_mcp(method, path, mcp_auth):
    response = make_client().request(method, path, headers=mcp_auth)
    assert response.status_code == 200
    assert response.json() == {"auth": "bearer", "key": "mcp", "session": False}


@pytest.mark.parametrize(("method", "path"), NOT_MCP_ROUTES)
def test_mcp_key_on_any_other_route_is_a_401(method, path, mcp_auth):
    assert make_client().request(method, path, headers=mcp_auth).status_code == 401


def test_mcp_key_on_approve_is_a_401(mcp_auth, bridge_auth):
    client = make_client()
    assert client.post("/api/reports/rep_1/approve", headers=mcp_auth).status_code == 401
    assert client.post("/api/reports/rep_1/approve", headers=bridge_auth).status_code == 200


def test_wrong_or_empty_bearer_is_a_401_everywhere(basic_auth):
    client = make_client()
    cookie = session_cookie(client, basic_auth)
    for token in ("wrong", "", "test-bridge-key-", "test-api-key"):
        headers = {"Authorization": f"Bearer {token}"}
        assert client.post("/api/intake/text", headers=headers).status_code == 401
        assert client.post("/mcp", headers=headers).status_code == 401
        assert client.get("/", headers=headers).status_code == 401
        # A bearer token is judged alone: a valid cookie beside a wrong token does not help.
        assert client.get("/", headers={**headers, "Cookie": cookie}).status_code == 401


def test_bearer_key_beside_a_cookie_stays_inside_its_scope(basic_auth, bridge_auth):
    client = make_client()
    headers = {**bridge_auth, "Cookie": session_cookie(client, basic_auth)}
    assert client.get("/api/reports", headers=headers).status_code == 401


def test_empty_keys_never_match(monkeypatch):
    monkeypatch.setenv("HACK_BRIDGE_KEY", "")
    monkeypatch.setenv("HACK_MCP_KEY", "")
    get_settings.cache_clear()
    client = make_client()
    for headers in ({"Authorization": "Bearer "}, {"Authorization": "Bearer"}):
        assert client.post("/api/intake/text", headers=headers).status_code == 401
        assert client.post("/mcp", headers=headers).status_code == 401


def test_key_scope_ignores_paths_that_are_not_plain():
    assert key_allowed("bridge", "http", "GET", "/api/files/reports/rep_1/f_1.docx")
    assert key_allowed("bridge", "http", "POST", "/api/intake/audio")
    assert key_allowed("mcp", "http", "POST", "/mcp")
    for path in (
        "/api/intake/../reports",
        "/api/files/../reports",
        "/api/files/./x",
        "/api/files/..\\..\\app.db",
        "/api/reports/../report-types",
    ):
        assert not key_allowed("bridge", "http", "GET", path), path
    assert not key_allowed("mcp", "http", "POST", "/mcp/../api/reports")
    assert not key_allowed("mcp", "websocket", "GET", "/mcp")
    assert not key_allowed("bridge", "websocket", "GET", "/api/intake/audio")
    assert not key_allowed("bridge", "http", "GET", "/api/call/ws")


def test_require_key_refuses_a_browser_session_and_the_other_key(basic_auth, bridge_auth):
    client = make_client()
    cookie = {"Cookie": session_cookie(client, basic_auth), "Origin": TEST_ORIGIN}

    assert client.post("/api/whatsapp/session", headers=bridge_auth).json() == {"ok": True}
    assert client.get("/api/whatsapp/pending", headers=bridge_auth).json() == {"ok": True}
    for response in (
        client.post("/api/whatsapp/session", headers=cookie),
        client.get("/api/whatsapp/pending", headers=cookie),
        client.post("/mcp/only", headers=cookie),
    ):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"

    with pytest.raises(ValueError):
        require_key("admin")  # type: ignore[arg-type]


# WebSocket ----------------------------------------------------------------------------------


def test_websocket_without_credentials_is_closed_with_4401():
    assert ws_close_code(make_client(), "/api/call/ws") == 4401


def test_websocket_with_basic_credentials_only_is_refused(basic_auth):
    assert ws_close_code(make_client(), "/api/call/ws", basic_auth) == 4401


def test_websocket_with_cookie_and_own_origin_is_accepted(basic_auth):
    client = make_client()
    headers = {"Cookie": session_cookie(client, basic_auth), "Origin": TEST_ORIGIN}
    with client.websocket_connect("/api/call/ws", headers=headers) as websocket:
        assert websocket.receive_json() == {"auth": "cookie", "key": None}


@pytest.mark.parametrize("origin", [None, "https://evil.example", "null", "http://localhost:3000"])
def test_websocket_with_cookie_and_a_foreign_origin_closes_with_4403(origin, basic_auth):
    client = make_client()
    headers = {"Cookie": session_cookie(client, basic_auth)}
    if origin:
        headers["Origin"] = origin
    assert ws_close_code(client, "/api/call/ws", headers) == 4403


def test_websocket_with_bridge_key_is_accepted_on_the_call_only(bridge_auth, mcp_auth):
    client = make_client()
    with client.websocket_connect("/api/call/ws", headers=dict(bridge_auth)) as websocket:
        assert websocket.receive_json() == {"auth": "bearer", "key": "bridge"}
    foreign = {**bridge_auth, "Origin": "https://evil.example"}  # bearer: Origin is not checked
    with client.websocket_connect("/api/call/ws", headers=foreign) as websocket:
        assert websocket.receive_json()["key"] == "bridge"

    assert ws_close_code(client, "/api/other/ws", bridge_auth) == 4401
    assert ws_close_code(client, "/api/call/ws", mcp_auth) == 4401
    assert ws_close_code(client, "/api/call/ws", {"Authorization": "Bearer wrong"}) == 4401


# switches -----------------------------------------------------------------------------------


def test_auth_disabled_lets_everything_in(monkeypatch):
    monkeypatch.setenv("HACK_AUTH_DISABLED", "1")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8787")
    get_settings.cache_clear()
    client = make_client()
    assert client.get("/").json()["auth"] == "disabled"
    assert client.post("/api/whoami").json()["auth"] == "disabled"
    assert client.post("/api/whatsapp/session").status_code == 200
    assert client.post("/mcp/only").status_code == 200
    with client.websocket_connect("/api/call/ws") as websocket:
        assert websocket.receive_json()["auth"] == "disabled"


def test_dev_cors_answers_the_preflight_and_marks_responses(monkeypatch, basic_auth):
    local_settings(monkeypatch, dev=True)
    client = make_client()
    cookie = {"Cookie": session_cookie(client, basic_auth)}
    origin = {"Origin": "http://localhost:3000"}

    preflight = client.options(
        "/api/whoami",
        headers={
            **origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert preflight.headers["access-control-allow-credentials"] == "true"

    response = client.post("/api/whoami", headers={**origin, **cookie})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"

    refused = client.get("/api/whoami", headers=origin)
    assert refused.status_code == 401
    assert refused.headers["access-control-allow-origin"] == "http://localhost:3000"

    other = client.get("/api/health", headers={"Origin": "https://elsewhere.example"})
    assert "access-control-allow-origin" not in other.headers


def test_no_cors_outside_dev():
    response = make_client().get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert "access-control-allow-origin" not in response.headers
