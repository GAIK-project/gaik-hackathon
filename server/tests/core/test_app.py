from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import textwrap
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app as app_package
from app.core import lifecycle
from app.core.settings import SERVER_ROOT, get_settings
from app.main import StartupRefused, configure_logging, create_app

BASE_URL = "https://testserver"
TEST_ORIGIN = "https://gaik-hackathon.example"  # PUBLIC_BASE_URL of the test settings


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    monkeypatch.setattr(lifecycle, "_registrations", dict(lifecycle._registrations))
    monkeypatch.setattr(lifecycle, "_mounts", dict(lifecycle._mounts))


@pytest.fixture
def feature_folder(tmp_path, monkeypatch):
    """A folder that is part of the `app` package for one test: write features into it."""
    monkeypatch.setattr(app_package, "__path__", [*app_package.__path__, str(tmp_path)])

    def write(name: str, router_source: str | None) -> None:
        folder = tmp_path / name
        folder.mkdir()
        (folder / "__init__.py").write_text("", encoding="utf-8")
        if router_source is not None:
            (folder / "router.py").write_text(textwrap.dedent(router_source), encoding="utf-8")

    return write


# health and features -----------------------------------------------------------------------


def test_health_is_open_and_says_status_auth_and_features(anon_client):
    response = anon_client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "auth", "features"}
    assert body["status"] == "ok"
    assert body["auth"] is True
    assert set(body["features"]) == set(lifecycle.FEATURES)
    assert all(isinstance(value, bool) for value in body["features"].values())


def test_health_says_when_access_control_is_off(monkeypatch):
    monkeypatch.setenv("HACK_AUTH_DISABLED", "1")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8787")
    get_settings.cache_clear()
    with TestClient(create_app(), base_url=BASE_URL) as client:
        assert client.get("/api/health").json()["auth"] is False


# startup refusal ---------------------------------------------------------------------------

LOCAL = "http://localhost:8787"


@pytest.mark.parametrize(
    ("env", "named"),
    [
        ({"HACK_AUTH_DISABLED": "1"}, "HACK_AUTH_DISABLED"),
        ({"HACK_DEV": "1"}, "HACK_DEV"),
        ({"HACK_AUTH_DISABLED": "1", "PUBLIC_BASE_URL": "https://localhost:8787"}, "HACK_AUTH"),
        ({"HACK_DEV": "1", "PUBLIC_BASE_URL": "http://localhost.evil.example"}, "HACK_DEV"),
        ({"HACK_DEV": "1", "PUBLIC_BASE_URL": "http://192.168.1.10:8787"}, "HACK_DEV"),
        ({"HACK_AUTH_DISABLED": "1", "PUBLIC_BASE_URL": ""}, "PUBLIC_BASE_URL"),
        ({"PUBLIC_BASE_URL": ""}, "PUBLIC_BASE_URL"),
        ({"PUBLIC_BASE_URL": "gaik-hackathon.example"}, "PUBLIC_BASE_URL"),
        ({"HACK_SESSION_SECRET": "x" * 31}, "HACK_SESSION_SECRET"),
        ({"HACK_SESSION_SECRET": ""}, "HACK_SESSION_SECRET"),
        ({"HACK_BRIDGE_KEY": ""}, "HACK_BRIDGE_KEY"),
        ({"HACK_MCP_KEY": ""}, "HACK_MCP_KEY"),
        ({"HACK_MCP_KEY": "   "}, "HACK_MCP_KEY"),
        ({"HACK_BASIC_USER": ""}, "HACK_BASIC_USER"),
        ({"HACK_BASIC_PASSWORD": ""}, "HACK_BASIC_PASSWORD"),
        ({"HACK_BRIDGE_KEY": "same-key", "HACK_MCP_KEY": "same-key"}, "must differ"),
        ({"MAIL_DEFAULT_TO": "muu@example.com"}, "MAIL_DEFAULT_TO"),
        # Local development does not excuse a weak secret or a missing key.
        ({"PUBLIC_BASE_URL": LOCAL, "HACK_AUTH_DISABLED": "1", "HACK_BRIDGE_KEY": ""}, "BRIDGE"),
        ({"PUBLIC_BASE_URL": LOCAL, "HACK_DEV": "1", "HACK_SESSION_SECRET": "short"}, "SECRET"),
    ],
)
def test_server_refuses_to_start(env, named, monkeypatch):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    with pytest.raises(StartupRefused) as refused:
        create_app()
    message = str(refused.value)
    assert message.startswith("Refusing to start: ")
    assert named in message
    # The message names settings; it never repeats a value.
    for secret in ("test-password", "test-session-secret", "test-bridge-key", "test-mcp-key"):
        assert secret not in message
    assert "same-key" not in message and "x" * 31 not in message


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"PUBLIC_BASE_URL": LOCAL, "HACK_AUTH_DISABLED": "1", "HACK_DEV": "1"},
        {"PUBLIC_BASE_URL": "http://127.0.0.1:8787", "HACK_AUTH_DISABLED": "1", "HACK_DEV": "1"},
        {"PUBLIC_BASE_URL": "http://localhost", "HACK_DEV": "1"},
        {"HACK_SESSION_SECRET": "x" * 32},
        {"MAIL_ALLOWED_TO": "a@example.com,b@example.com", "MAIL_DEFAULT_TO": "B@example.com"},
        {"MAIL_ALLOWED_TO": "", "MAIL_DEFAULT_TO": ""},
    ],
)
def test_server_starts_with_sound_settings(env, monkeypatch):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    assert isinstance(create_app(), FastAPI)


def test_the_served_app_is_built_on_first_use_and_refuses_bad_settings():
    # `uvicorn app.main:app` imports the module and reads `app`: that is where it stops.
    code = "import app.main as main\nprint('imported')\nmain.app\nprint('built')\n"
    bad = _run(code, HACK_SESSION_SECRET="short-secret-value", HACK_BRIDGE_KEY="")
    assert bad.returncode != 0
    assert bad.stdout.split() == ["imported"]
    assert "StartupRefused" in bad.stderr and "Refusing to start" in bad.stderr
    assert "HACK_SESSION_SECRET" in bad.stderr and "HACK_BRIDGE_KEY" in bad.stderr
    assert "short-secret-value" not in bad.stderr

    good = _run(code)
    assert good.returncode == 0, good.stderr[-2000:]
    assert good.stdout.split() == ["imported", "built"]


def _run(code: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=SERVER_ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=180,
    )


# access log --------------------------------------------------------------------------------


def _access_line(path_with_query: str) -> str:
    """The line uvicorn's access logger writes for a request, through the app's filter."""
    configure_logging()
    logger = logging.getLogger("uvicorn.access")
    record = logger.makeRecord(
        logger.name,
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:50000", "GET", path_with_query, "1.1", 200),
        None,
    )
    assert logger.filter(record)
    return record.getMessage()


def test_access_log_drops_the_query_string_of_whatsapp_paths():
    line = _access_line("/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=VERIFY-TOKEN")
    assert line == '127.0.0.1:50000 - "GET /api/whatsapp/webhook HTTP/1.1" 200'
    assert "?" not in _access_line("/api/whatsapp/pending?wait=25")
    assert _access_line("/api/whatsapp/status") == (
        '127.0.0.1:50000 - "GET /api/whatsapp/status HTTP/1.1" 200'
    )


def test_access_log_keeps_other_query_strings():
    assert "/api/reports?limit=50" in _access_line("/api/reports?limit=50")
    assert "/api/whatsappx?token=1" in _access_line("/api/whatsappx?token=1")


def test_access_log_filter_is_installed_once():
    configure_logging()
    configure_logging()
    filters = logging.getLogger("uvicorn.access").filters
    assert sum(type(item).__name__ == "_DropWhatsAppQuery" for item in filters) == 1


async def test_uvicorn_itself_logs_whatsapp_paths_without_the_query(caplog):
    """Drive uvicorn's HTTP protocol with a fake transport (no socket): the real access-log
    call goes through the filter."""
    from uvicorn.config import Config
    from uvicorn.protocols.http.h11_impl import H11Protocol
    from uvicorn.server import ServerState

    class Transport(asyncio.Transport):
        def __init__(self) -> None:
            super().__init__()
            self.written = b""
            self.closed = False

        def get_extra_info(self, name, default=None):
            return {"peername": ("127.0.0.1", 50000), "sockname": ("127.0.0.1", 8787)}.get(
                name, default
            )

        def write(self, data) -> None:
            self.written += data

        def close(self) -> None:
            self.closed = True

        def is_closing(self) -> bool:
            return self.closed

        def pause_reading(self) -> None: ...

        def resume_reading(self) -> None: ...

    config = Config(app=create_app(), log_config=None, access_log=True, lifespan="off")
    config.load()

    async def request(target: str) -> bytes:
        protocol = H11Protocol(config=config, server_state=ServerState(), app_state={})
        transport = Transport()
        protocol.connection_made(transport)
        protocol.data_received(
            f"GET {target} HTTP/1.1\r\nHost: gaik-hackathon.example\r\n"
            "Connection: close\r\n\r\n".encode()
        )
        for _ in range(200):
            if b"\r\n\r\n" in transport.written and transport.closed:
                break
            await asyncio.sleep(0.01)
        return transport.written

    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        response = await request(
            "/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=VERIFY-TOKEN"
        )
        await request("/api/health?probe=1")

    assert response.startswith(b"HTTP/1.1 ")
    lines = [record.getMessage() for record in caplog.records if record.name == "uvicorn.access"]
    assert len(lines) == 2
    assert '"GET /api/whatsapp/webhook HTTP/1.1"' in lines[0]
    assert "VERIFY-TOKEN" not in caplog.text and "hub." not in caplog.text
    assert '"GET /api/health?probe=1 HTTP/1.1" 200' in lines[1]


def test_features_that_import_are_included_and_the_rest_do_not_stop_the_app(feature_folder, caplog):
    feature_folder(
        "t_good",
        """
        from fastapi import APIRouter
        router = APIRouter()

        @router.get("/api/t-good/ping")
        async def ping():
            return {"pong": True}
        """,
    )
    feature_folder("t_no_router_module", None)
    feature_folder("t_broken", "raise RuntimeError('boom')\n")
    feature_folder("t_needs_package", "import a_package_that_is_not_installed\n")
    feature_folder("t_wrong_type", "router = 'not a router'\n")
    names = (
        "t_good",
        "t_no_router_module",
        "t_broken",
        "t_needs_package",
        "t_wrong_type",
        "t_absent",
    )

    app = FastAPI()
    with caplog.at_level("WARNING"):
        included = lifecycle.include_features(app, names)

    assert included == {name: name == "t_good" for name in names}
    assert TestClient(app).get("/api/t-good/ping").json() == {"pong": True}
    messages = {record.levelname + " " + record.getMessage() for record in caplog.records}
    assert "WARNING Feature t_absent is not there yet (app.t_absent is missing)" in messages
    assert any(m.startswith("WARNING Feature t_no_router_module is not there") for m in messages)
    assert any(m.startswith("ERROR Feature t_broken failed to import") for m in messages)
    assert any(m.startswith("ERROR Feature t_needs_package needs a module") for m in messages)
    assert any(m.startswith("ERROR Feature t_wrong_type") for m in messages)


# startup and shutdown ----------------------------------------------------------------------


def test_registered_work_runs_in_order_around_the_app():
    calls: list[str] = []

    @asynccontextmanager
    async def session_manager():
        calls.append("context in")
        yield
        calls.append("context out")

    async def async_start():
        calls.append("async start")

    lifecycle.register(
        "first",
        startup=lambda: calls.append("first start"),
        shutdown=lambda: calls.append("first stop"),
    )
    lifecycle.register("second", startup=async_start, context=session_manager)

    async def async_stop():
        calls.append("third stop")

    lifecycle.register("third", shutdown=async_stop)

    with TestClient(FastAPI(lifespan=lifecycle.lifespan)):
        assert calls == ["first start", "context in", "async start"]
    assert calls[3:] == ["third stop", "context out", "first stop"]


def test_failing_startup_marks_the_feature_and_the_app_still_starts():
    stopped: list[str] = []

    def fail():
        raise RuntimeError("corpus missing")

    def fail_on_stop():
        raise RuntimeError("stop failed")

    lifecycle.register("aiact", startup=fail, shutdown=lambda: stopped.append("aiact"))
    lifecycle.register("reports", shutdown=fail_on_stop)
    lifecycle.register("studio", shutdown=lambda: stopped.append("studio"))

    app = FastAPI(lifespan=lifecycle.lifespan)
    app.state.features = {"aiact": True, "reports": True, "studio": True}
    with TestClient(app):
        assert app.state.features == {"aiact": False, "reports": True, "studio": True}
    assert stopped == ["studio"]  # a failed startup has no shutdown; a failing shutdown is logged


def test_registering_a_name_again_replaces_it():
    calls: list[str] = []
    lifecycle.register("mcp", startup=lambda: calls.append("old"))
    lifecycle.register("mcp", startup=lambda: calls.append("new"))
    with TestClient(FastAPI(lifespan=lifecycle.lifespan)):
        pass
    assert calls == ["new"]


# raw ASGI apps -----------------------------------------------------------------------------


def test_mounted_asgi_app_serves_its_exact_path_behind_the_access_rules(
    mcp_auth, bridge_auth, session_auth
):
    seen: list[str] = []

    async def fake_mcp(scope, receive, send):
        seen.append(scope["path"])
        body = b'{"jsonrpc":"2.0"}'
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    lifecycle.mount_asgi("/mcp", fake_mcp)
    with TestClient(create_app(), base_url=BASE_URL, follow_redirects=False) as client:
        for path in ("/mcp", "/mcp/"):
            response = client.post(path, headers=mcp_auth, json={})
            assert response.status_code == 200
            assert response.json() == {"jsonrpc": "2.0"}
        assert seen == ["/mcp", "/mcp"]

        refused = client.post("/mcp", json={})
        assert refused.status_code == 401
        assert client.post("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.post("/mcp", headers=bridge_auth, json={}).status_code == 401
        assert client.post("/mcp/other", headers=mcp_auth).status_code in (404, 405)
        assert seen == ["/mcp", "/mcp"]

        # A signed-in browser reaches it too, with the app's own Origin like any other write.
        assert client.post("/mcp", headers=session_auth, json={}).status_code == 200
        no_origin = {"Cookie": session_auth["Cookie"]}
        assert client.post("/mcp", headers=no_origin, json={}).status_code == 403
        # The body limit sits in front of mounted apps as well.
        huge = client.post("/mcp", headers=mcp_auth, content=b"x" * (22 * 1024 * 1024))
        assert huge.status_code == 413
        assert len(seen) == 3

    with pytest.raises(ValueError):
        lifecycle.mount_asgi("/", fake_mcp)


# routes of the app itself ------------------------------------------------------------------


def test_unknown_api_path_is_a_json_404(client, anon_client):
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "We could not find what you were looking for."}
    }
    assert anon_client.get("/api/does-not-exist").status_code == 401


def test_exported_front_end_is_served_last_and_needs_a_login(
    tmp_path, basic_auth, bridge_auth, mcp_auth
):
    web = tmp_path / "out"
    (web / "soita").mkdir(parents=True)
    (web / "_next").mkdir()
    (web / "index.html").write_text("<h1>Hub</h1>", encoding="utf-8")
    (web / "soita" / "index.html").write_text("<h1>Soita</h1>", encoding="utf-8")
    (web / "404.html").write_text("<h1>Ei löytynyt</h1>", encoding="utf-8")
    (web / "_next" / "app.js").write_text("console.log(1)", encoding="utf-8")

    with TestClient(create_app(web_dir=web), base_url=BASE_URL) as client:
        refused = client.get("/")
        assert refused.status_code == 401
        assert refused.headers["www-authenticate"].startswith("Basic")
        assert client.get("/_next/app.js").status_code == 401
        assert client.get("/", headers=bridge_auth).status_code == 401
        assert client.get("/", headers=mcp_auth).status_code == 401

        assert client.get("/api/health").status_code == 200  # still the API, not a file

        home = client.get("/", headers=basic_auth)  # sets the cookie for what follows
        assert home.status_code == 200 and "Hub" in home.text
        assert "Soita" in client.get("/soita/").text
        assert client.get("/_next/app.js").text == "console.log(1)"
        missing = client.get("/ei-ole/")
        assert missing.status_code == 404 and "Ei löytynyt" in missing.text
        assert client.get("/api/nothing").json()["error"]["code"] == "not_found"


def test_app_without_a_built_front_end_still_serves_the_api(tmp_path):
    with TestClient(create_app(web_dir=tmp_path / "missing"), base_url=BASE_URL) as client:
        assert client.get("/api/health").status_code == 200
        response = client.get("/", headers={"Cookie": "__Host-gh_session=x"})
        assert response.status_code == 401


def test_static_files_are_not_reached_through_a_key_or_a_dot_path(tmp_path, bridge_auth):
    web = tmp_path / "out"
    web.mkdir()
    (web / "index.html").write_text("<h1>Hub</h1>", encoding="utf-8")
    with TestClient(create_app(web_dir=web), base_url=BASE_URL) as client:
        for path in ("/index.html", "/api/intake/../index.html", "/api/files/../../index.html"):
            for method in ("GET", "HEAD"):
                response = client.request(method, path, headers=bridge_auth)
                assert response.status_code == 401, (method, path)


# the fixtures feature tests use ------------------------------------------------------------

ECHO_FEATURE = """
    from fastapi import APIRouter, Request, WebSocket
    router = APIRouter()

    @router.api_route("/api/intake/text", methods=["GET", "POST"])
    @router.api_route("/api/reports", methods=["GET", "POST"])
    async def how(request: Request):
        return {"auth": request.state.auth, "key": request.state.key}

    @router.websocket("/api/call/ws")
    async def call(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json({"auth": websocket.state.auth, "key": websocket.state.key})
        await websocket.close()
"""


@pytest.fixture
def echo_feature(feature_folder, monkeypatch):
    feature_folder("t_echo", ECHO_FEATURE)
    # First in the list: its routes then answer before a real feature's at the same paths.
    features = ("t_echo", *lifecycle.FEATURES)
    monkeypatch.setattr(lifecycle, "FEATURES", features)
    monkeypatch.setattr(lifecycle.include_features, "__defaults__", (features,))


def test_client_fixture_is_a_signed_in_browser(echo_feature, client):
    assert client.get("/api/reports").json() == {"auth": "cookie", "key": None}
    assert client.post("/api/reports").json() == {"auth": "cookie", "key": None}  # Origin is sent
    assert client.post("/api/intake/text").json()["auth"] == "cookie"
    with client.websocket_connect("/api/call/ws") as websocket:
        assert websocket.receive_json() == {"auth": "cookie", "key": None}
    assert client.get("/api/health").json()["auth"] is True


def test_bridge_client_fixture_holds_the_bridge_key(echo_feature, bridge_client):
    assert bridge_client.post("/api/intake/text").json() == {"auth": "bearer", "key": "bridge"}
    assert bridge_client.get("/api/reports").status_code == 401
    assert bridge_client.post("/mcp").status_code == 401
    with bridge_client.websocket_connect("/api/call/ws") as websocket:
        assert websocket.receive_json() == {"auth": "bearer", "key": "bridge"}


def test_mcp_client_fixture_holds_the_mcp_key(echo_feature, mcp_client):
    seen: list[str] = []

    async def fake_mcp(scope, receive, send):
        seen.append(scope["state"]["key"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    lifecycle.mount_asgi("/mcp", fake_mcp)
    assert mcp_client.post("/mcp", json={}).status_code == 200
    assert seen == ["mcp"]
    assert mcp_client.post("/api/intake/text").status_code == 401
    assert mcp_client.post("/api/reports/rep_1/approve").status_code == 401
    assert mcp_client.get("/api/reports").status_code == 401


def test_anon_client_fixture_has_no_credentials(echo_feature, anon_client):
    assert anon_client.get("/api/reports").status_code == 401
    assert anon_client.post("/api/intake/text").status_code == 401
    assert anon_client.get("/api/health").status_code == 200
