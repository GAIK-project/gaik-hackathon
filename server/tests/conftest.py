"""Shared fixtures.

Every test runs with its own `DATA_DIR`, access control switched on with the known test
credentials below, mail disabled, models on and every limit counter at zero. Model settings
(`AZURE_*`, `GAIK_DECIDE_*`) are left as the environment has them, so tests marked `live` can
make real calls.

    def test_page(client):                 # a signed-in browser: session cookie + Origin
        assert client.post("/api/intake/text", json={...}).status_code == 200

    def test_bridge(bridge_client):        # Authorization: Bearer <HACK_BRIDGE_KEY>
        bridge_client.post("/api/whatsapp/session", json={...})

    def test_agent(mcp_client):            # Authorization: Bearer <HACK_MCP_KEY>, /mcp only
        mcp_client.post("/mcp", json={...})

    def test_refused(anon_client, basic_auth):    # no credentials unless the test adds them
        anon_client.get("/", headers=basic_auth)

All four clients share one app (the `app` fixture) and run its startup and shutdown work. A
WebSocket opened from `client` carries the cookie and the Origin too.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import limits
from app.core.auth import cookie_name, sign_session
from app.core.db import close_store
from app.core.settings import Settings, get_settings

TEST_USER = "test-user"
TEST_PASSWORD = "test-password"
TEST_SESSION_SECRET = "test-session-secret-0123456789abcdef"  # 32 characters or more
TEST_BRIDGE_KEY = "test-bridge-key"
TEST_MCP_KEY = "test-mcp-key"
TEST_RECIPIENT = "testi@example.com"

# The public origin of the app under test: what a browser sends as `Origin`.
TEST_ORIGIN = "https://gaik-hackathon.example"
# The Secure session cookie is only sent back over https.
BASE_URL = "https://testserver"


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HACK_AUTH_DISABLED", "0")
    monkeypatch.setenv("HACK_DEV", "0")
    monkeypatch.setenv("HACK_MODELS_OFF", "0")
    monkeypatch.setenv("HACK_BASIC_USER", TEST_USER)
    monkeypatch.setenv("HACK_BASIC_PASSWORD", TEST_PASSWORD)
    monkeypatch.setenv("HACK_SESSION_SECRET", TEST_SESSION_SECRET)
    monkeypatch.setenv("HACK_BRIDGE_KEY", TEST_BRIDGE_KEY)
    monkeypatch.setenv("HACK_MCP_KEY", TEST_MCP_KEY)
    monkeypatch.setenv("PUBLIC_BASE_URL", TEST_ORIGIN)
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("MAIL_FROM", "lahettaja@example.com")
    monkeypatch.setenv("MAIL_ALLOWED_TO", TEST_RECIPIENT)
    monkeypatch.setenv("MAIL_DEFAULT_TO", "")
    monkeypatch.setenv("MAIL_MAX_PER_HOUR", "10")
    get_settings.cache_clear()
    close_store()
    limits.reset()
    yield
    close_store()
    limits.reset()
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def bridge_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_BRIDGE_KEY}"}


@pytest.fixture
def mcp_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_MCP_KEY}"}


@pytest.fixture
def basic_auth() -> dict[str, str]:
    token = base64.b64encode(f"{TEST_USER}:{TEST_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def session_auth(settings: Settings) -> dict[str, str]:
    """What a signed-in browser sends: a fresh session cookie and the app's own Origin."""
    value = sign_session(settings.hack_session_secret)
    return {"Cookie": f"{cookie_name(settings)}={value}", "Origin": TEST_ORIGIN}


@pytest.fixture
def app() -> FastAPI:
    """The real app, built from the test settings."""
    from app.main import create_app

    return create_app()


@pytest.fixture
def client(app: FastAPI, session_auth: dict[str, str]) -> Iterator[TestClient]:
    """A signed-in browser. Startup and shutdown work runs around the test."""
    with TestClient(app, base_url=BASE_URL, headers=session_auth) as test_client:
        yield test_client


@pytest.fixture
def bridge_client(app: FastAPI, bridge_auth: dict[str, str]) -> Iterator[TestClient]:
    """The WhatsApp bridge: the bridge key, good only for the routes of section 15.2."""
    with TestClient(app, base_url=BASE_URL, headers=bridge_auth) as test_client:
        yield test_client


@pytest.fixture
def mcp_client(app: FastAPI, mcp_auth: dict[str, str]) -> Iterator[TestClient]:
    """An agent: the MCP key, good only for `/mcp`."""
    with TestClient(app, base_url=BASE_URL, headers=mcp_auth) as test_client:
        yield test_client


@pytest.fixture
def anon_client(app: FastAPI) -> Iterator[TestClient]:
    """A client without credentials."""
    with TestClient(app, base_url=BASE_URL) as test_client:
        yield test_client
