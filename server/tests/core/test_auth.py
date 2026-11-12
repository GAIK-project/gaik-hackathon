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
    install_auth,
    require_bearer,
    sign_session,
    verify_session,
)
from app.core.errors import install_error_handlers
from app.core.settings import Settings, get_settings

BASE_URL = "https://testserver"
CHALLENGE = 'Basic realm="GAIK mini-hackathon"'


def make_client(settings: Settings | None = None) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.api_route("/api/whatsapp/webhook", methods=["GET", "POST", "DELETE"])
    async def webhook() -> dict[str, bool]:
        return {"stored": True}

    @app.get("/api/whoami")
    async def whoami(request: Request) -> dict[str, str]:
        return {"auth": request.state.auth}

    @app.post("/api/bridge-only", dependencies=[Depends(require_bearer)])
    async def bridge_only() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/mcp")
    async def mcp(request: Request) -> dict[str, str]:
        return {"auth": request.state.auth}

    @app.get("/")
    async def page(request: Request) -> dict[str, str]:
        return {"auth": request.state.auth}

    @app.websocket("/api/call/ws")
    async def call(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({"auth": websocket.state.auth})
        await websocket.close()

    install_auth(app, settings or get_settings())
    return TestClient(app, base_url=BASE_URL)


def session_cookie(client: TestClient, basic_auth: dict[str, str]) -> str:
    response = client.get("/", headers=basic_auth)
    assert response.status_code == 200
    value = response.cookies.get(COOKIE_NAME)
    assert value
    client.cookies.clear()  # tests pass the cookie explicitly
    return value


def test_open_paths_need_nothing():
    client = make_client()
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/whatsapp/webhook").status_code == 200
    assert client.post("/api/whatsapp/webhook", content=b"{}").status_code == 200
    assert client.get("/api/health").json() == {"status": "ok"}


def test_open_paths_are_open_for_their_methods_only():
    assert make_client().delete("/api/whatsapp/webhook").status_code == 401


@pytest.mark.parametrize("path", ["/", "/api/whoami"])
def test_no_credentials_gets_401_with_the_basic_challenge(path):
    response = make_client().get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == CHALLENGE
    assert response.json() == {"error": {"code": "unauthorized", "message": "Kirjaudu sisään."}}
    assert "set-cookie" not in response.headers


def test_wrong_basic_credentials_are_refused():
    client = make_client()
    for user, password in (("test-user", "wrong"), ("wrong", "test-password"), ("", "")):
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        response = client.get("/", headers={"Authorization": f"Basic {token}"})
        assert response.status_code == 401
        assert "set-cookie" not in response.headers
    assert client.get("/", headers={"Authorization": "Basic not-base64!"}).status_code == 401


def test_basic_credentials_pass_and_set_the_session_cookie(basic_auth):
    response = make_client().get("/", headers=basic_auth)
    assert response.status_code == 200
    assert response.json() == {"auth": "basic"}
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}=")
    attributes = {part.strip().lower() for part in cookie.split(";")[1:]}
    assert attributes == {
        "path=/",
        f"max-age={COOKIE_MAX_AGE}",
        "httponly",
        "samesite=lax",
        "secure",
    }
    assert COOKIE_MAX_AGE == 7 * 24 * 60 * 60


def test_cookie_is_not_secure_in_dev(basic_auth, monkeypatch):
    monkeypatch.setenv("HACK_DEV", "1")
    get_settings.cache_clear()
    cookie = make_client().get("/", headers=basic_auth).headers["set-cookie"]
    assert "secure" not in cookie.lower()


def test_session_cookie_is_accepted(basic_auth):
    client = make_client()
    cookie = session_cookie(client, basic_auth)
    for path in ("/", "/api/whoami"):
        response = client.get(path, headers={"Cookie": f"{COOKIE_NAME}={cookie}"})
        assert response.status_code == 200
        assert response.json() == {"auth": "cookie"}
        assert "set-cookie" not in response.headers


def test_browser_flow_keeps_the_cookie(basic_auth):
    client = make_client()
    assert client.get("/", headers=basic_auth).status_code == 200
    assert client.get("/").json() == {"auth": "cookie"}  # the jar sends it back


def test_tampered_or_expired_cookie_is_refused(basic_auth, settings):
    client = make_client()
    cookie = session_cookie(client, basic_auth)
    tampered = cookie[:-1] + ("0" if cookie[-1] != "0" else "1")
    expired = sign_session(settings.hack_session_secret, now=time.time() - COOKIE_MAX_AGE - 5)
    forged = sign_session("another-secret")
    for value in (tampered, expired, forged, "x", ""):
        response = client.get("/", headers={"Cookie": f"{COOKIE_NAME}={value}"})
        assert response.status_code == 401, value


def test_sign_and_verify(settings):
    secret = settings.hack_session_secret
    value = sign_session(secret)
    assert verify_session(secret, value)
    assert not verify_session(secret, value, now=time.time() + COOKIE_MAX_AGE + 1)
    assert not verify_session("", value)
    assert not verify_session(secret, None)


def test_bearer_is_accepted_on_api_and_mcp(bearer_auth):
    client = make_client()
    assert client.get("/api/whoami", headers=bearer_auth).json() == {"auth": "bearer"}
    assert client.post("/mcp", headers=bearer_auth).json() == {"auth": "bearer"}


def test_bearer_is_not_a_browser_login(bearer_auth):
    client = make_client()
    assert client.get("/", headers=bearer_auth).status_code == 401
    assert client.get("/api/whoami", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/mcp", headers={"Authorization": "Bearer "}).status_code == 401
    assert client.post("/mcp").status_code == 401


def test_empty_api_key_never_matches(monkeypatch):
    monkeypatch.setenv("HACK_API_KEY", "")
    get_settings.cache_clear()
    response = make_client().get("/api/whoami", headers={"Authorization": "Bearer "})
    assert response.status_code == 401


def test_require_bearer_refuses_a_browser_session(basic_auth, bearer_auth):
    client = make_client()
    assert client.post("/api/bridge-only", headers=bearer_auth).status_code == 200
    response = client.post("/api/bridge-only", headers=basic_auth)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_websocket_without_cookie_is_closed_with_4401():
    client = make_client()
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/api/call/ws") as websocket:
            websocket.receive_json()
    assert closed.value.code == 4401


def test_websocket_with_basic_credentials_only_is_refused(basic_auth):
    client = make_client()
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/api/call/ws", headers=basic_auth) as websocket:
            websocket.receive_json()
    assert closed.value.code == 4401


def test_websocket_with_cookie_is_accepted(basic_auth):
    client = make_client()
    cookie = session_cookie(client, basic_auth)
    headers = {"Cookie": f"{COOKIE_NAME}={cookie}"}
    with client.websocket_connect("/api/call/ws", headers=headers) as websocket:
        assert websocket.receive_json() == {"auth": "cookie"}


def test_websocket_with_bearer_is_accepted_under_api(bearer_auth):
    client = make_client()
    with client.websocket_connect("/api/call/ws", headers=bearer_auth) as websocket:
        assert websocket.receive_json() == {"auth": "bearer"}


def test_auth_disabled_lets_everything_in(monkeypatch):
    monkeypatch.setenv("HACK_AUTH_DISABLED", "1")
    get_settings.cache_clear()
    client = make_client()
    assert client.get("/").json() == {"auth": "disabled"}
    assert client.post("/api/bridge-only").status_code == 200
    with client.websocket_connect("/api/call/ws") as websocket:
        assert websocket.receive_json() == {"auth": "disabled"}


def test_dev_cors_answers_the_preflight_and_marks_responses(monkeypatch, bearer_auth):
    monkeypatch.setenv("HACK_DEV", "1")
    get_settings.cache_clear()
    client = make_client()
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

    response = client.get("/api/whoami", headers={**origin, **bearer_auth})
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
