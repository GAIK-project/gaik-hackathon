from __future__ import annotations

import textwrap
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app as app_package
from app.core import lifecycle
from app.main import create_app

BASE_URL = "https://testserver"


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


def test_health_is_open_and_lists_every_feature(anon_client):
    response = anon_client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["features"]) == set(lifecycle.FEATURES)
    assert all(isinstance(value, bool) for value in body["features"].values())


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


def test_mounted_asgi_app_serves_its_exact_path_behind_the_access_rules(bearer_auth, basic_auth):
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
            response = client.post(path, headers=bearer_auth, json={})
            assert response.status_code == 200
            assert response.json() == {"jsonrpc": "2.0"}
        assert seen == ["/mcp", "/mcp"]

        refused = client.post("/mcp", json={})
        assert refused.status_code == 401
        assert client.post("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.post("/mcp/other", headers=bearer_auth).status_code in (404, 405)
        assert seen == ["/mcp", "/mcp"]

    with pytest.raises(ValueError):
        lifecycle.mount_asgi("/", fake_mcp)


# routes of the app itself ------------------------------------------------------------------


def test_unknown_api_path_is_a_json_404(client, anon_client):
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Etsimääsi ei löytynyt."}}
    assert anon_client.get("/api/does-not-exist").status_code == 401


def test_exported_front_end_is_served_last_and_needs_a_login(tmp_path, basic_auth, bearer_auth):
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
        assert client.get("/", headers=bearer_auth).status_code == 401

        assert client.get("/api/health").status_code == 200  # still the API, not a file

        home = client.get("/", headers=basic_auth)  # sets the cookie for what follows
        assert home.status_code == 200 and "Hub" in home.text
        assert "Soita" in client.get("/soita/").text
        assert client.get("/_next/app.js").text == "console.log(1)"
        missing = client.get("/ei-ole/")
        assert missing.status_code == 404 and "Ei löytynyt" in missing.text
        assert client.get("/api/nothing").json()["error"]["code"] == "not_found"


def test_app_without_a_built_front_end_still_serves_the_api(tmp_path, bearer_auth):
    with TestClient(create_app(web_dir=tmp_path / "missing"), base_url=BASE_URL) as client:
        assert client.get("/api/health").status_code == 200
        response = client.get("/", headers={"Cookie": "gh_session=x"})
        assert response.status_code == 401
