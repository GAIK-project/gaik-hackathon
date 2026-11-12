"""Shared fixtures.

Every test runs with its own `DATA_DIR`, access control switched on with the known test
credentials below, and mail disabled. Model settings (`AZURE_*`, `GAIK_DECIDE_*`) are left as
the environment has them, so tests marked `live` can make real calls.

    def test_something(client):            # TestClient that sends the bearer key
        assert client.get("/api/reports").status_code == 200

    def test_browser(anon_client, basic_auth):   # no credentials unless the test adds them
        anon_client.get("/", headers=basic_auth)
"""

from __future__ import annotations

import base64
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import close_store
from app.core.settings import Settings, get_settings

TEST_USER = "test-user"
TEST_PASSWORD = "test-password"
TEST_SESSION_SECRET = "test-session-secret"
TEST_API_KEY = "test-api-key"
TEST_RECIPIENT = "testi@example.com"

# The Secure session cookie is only sent back over https.
BASE_URL = "https://testserver"


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HACK_AUTH_DISABLED", "0")
    monkeypatch.setenv("HACK_DEV", "0")
    monkeypatch.setenv("HACK_BASIC_USER", TEST_USER)
    monkeypatch.setenv("HACK_BASIC_PASSWORD", TEST_PASSWORD)
    monkeypatch.setenv("HACK_SESSION_SECRET", TEST_SESSION_SECRET)
    monkeypatch.setenv("HACK_API_KEY", TEST_API_KEY)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://gaik-hackathon.example")
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("MAIL_FROM", "lahettaja@example.com")
    monkeypatch.setenv("MAIL_ALLOWED_TO", TEST_RECIPIENT)
    get_settings.cache_clear()
    close_store()
    yield
    close_store()
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def bearer_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_API_KEY}"}


@pytest.fixture
def basic_auth() -> dict[str, str]:
    token = base64.b64encode(f"{TEST_USER}:{TEST_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def app() -> FastAPI:
    """The real app, built from the test settings."""
    from app.main import create_app

    return create_app()


@pytest.fixture
def client(app: FastAPI, bearer_auth: dict[str, str]) -> Iterator[TestClient]:
    """A client that sends the bearer key; startup and shutdown work runs around the test."""
    with TestClient(app, base_url=BASE_URL, headers=bearer_auth) as test_client:
        yield test_client


@pytest.fixture
def anon_client(app: FastAPI) -> Iterator[TestClient]:
    """A client without credentials."""
    with TestClient(app, base_url=BASE_URL) as test_client:
        yield test_client
