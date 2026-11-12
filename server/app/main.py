"""The app: `create_app()` and `app`, the instance uvicorn serves.

    uv run python -m uvicorn app.main:app --port 8787        (from server/)

Request path, outermost first: dev CORS (only with `HACK_DEV=1`), the access rules of section
15.2, the request-body limits of section 15.3, raw ASGI apps registered with
`lifecycle.mount_asgi` (the MCP endpoint), then FastAPI: `/api/health`, stored files, every
feature router that imports, a JSON 404 for the rest of `/api`, and the statically exported
front end (`web/out`) at `/` when that folder exists.

`create_app` raises `StartupRefused` for settings that must not serve (an open or dev server
on a public address, a short session secret, an empty key), so the process never starts
with them.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

from app.core import files, lifecycle, limits
from app.core.auth import install_auth
from app.core.db import close_store
from app.core.errors import AppError, install_error_handlers
from app.core.events import get_bus
from app.core.settings import WEB_OUT_DIR, Settings, get_settings

logger = logging.getLogger(__name__)

_QUIET_LOGGERS = ("httpx", "httpx2", "httpcore", "openai", "urllib3", "websockets", "mcp")
_ACCESS_LOGGER = "uvicorn.access"
_NO_QUERY_PREFIX = "/api/whatsapp/"


class StartupRefused(RuntimeError):
    """The settings are not safe to serve with. The message names settings, never values."""


class _DropWhatsAppQuery(logging.Filter):
    """Cut the query string from access-log lines of `/api/whatsapp/*`: Meta's handshake
    carries the bridge session's verify token there.

    uvicorn logs `'%s - "%s %s HTTP/%s" %d'` with the path and query as the third argument.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            target = args[2]
            if target.startswith(_NO_QUERY_PREFIX) and "?" in target:
                record.args = (*args[:2], target.split("?", 1)[0], *args[3:])
        return True


def configure_logging() -> None:
    """Give the root logger a handler before gaik does (it calls `basicConfig` at import),
    keep HTTP client libraries from logging every request line, and keep the query string of
    `/api/whatsapp/*` out of the access log."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    access = logging.getLogger(_ACCESS_LOGGER)
    if not any(isinstance(item, _DropWhatsAppQuery) for item in access.filters):
        access.addFilter(_DropWhatsAppQuery())


def check_startup(settings: Settings) -> None:
    """Refuse settings the server must not start with (section 15.2)."""
    problems = settings.startup_problems()
    if problems:
        raise StartupRefused("Refusing to start: " + "; ".join(problems))


class _WebFiles(StaticFiles):
    """The exported front end. A WebSocket that matched no route ends here: refuse it."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        await super().__call__(scope, receive, send)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        async with lifecycle.lifespan(app):
            yield
            get_bus().close()  # open event streams end with `done` before features shut down
    finally:
        close_store()


def create_app(settings: Settings | None = None, *, web_dir: Path | None = None) -> FastAPI:
    """Build the app. `settings` and `web_dir` are for tests; the defaults are the real ones."""
    configure_logging()
    settings = settings or get_settings()
    check_startup(settings)
    if files.ffmpeg_path() is None:
        logger.warning("ffmpeg is not on the PATH: audio uploads cannot be transcribed")

    app = FastAPI(
        title="GAIK mini-hackathon",
        lifespan=_lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    install_error_handlers(app)

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "auth": not settings.hack_auth_disabled,
            "features": dict(app.state.features),
        }

    app.include_router(files.router)
    app.state.features = lifecycle.include_features(app)

    @app.api_route(
        "/api/{rest:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def unknown_api_path(rest: str) -> None:
        raise AppError("not_found", "Etsimääsi ei löytynyt.", 404)

    static_dir = web_dir or WEB_OUT_DIR
    if static_dir.is_dir():
        app.mount("/", _WebFiles(directory=static_dir, html=True), name="web")
    else:
        logger.warning("No built front end at web/out: only /api and /mcp are served")

    # Added last runs first:
    # (dev CORS) -> access rules -> body limits -> mounted ASGI apps -> routes.
    app.add_middleware(lifecycle.MountedApps)
    app.add_middleware(limits.BodyLimitMiddleware)
    install_auth(app, settings)
    return app


def __getattr__(name: str) -> FastAPI:
    """`app.main:app`, the app uvicorn serves. It is built on first use, so importing this
    module (for `create_app`, in tests) does not need settings the server could start with."""
    if name == "app":
        instance = create_app()
        globals()["app"] = instance
        return instance
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
