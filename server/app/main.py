"""The app: `create_app()` and the module-level `app` uvicorn serves.

    uv run python -m uvicorn app.main:app --port 8787        (from server/)

Request path, outermost first: dev CORS (only with `HACK_DEV=1`), the access rules of section
6, raw ASGI apps registered with `lifecycle.mount_asgi` (the MCP endpoint), then FastAPI:
`/api/health`, stored files, every feature router that imports, a JSON 404 for the rest of
`/api`, and the statically exported front end (`web/out`) at `/` when that folder exists.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

from app.core import files, lifecycle
from app.core.auth import install_auth
from app.core.db import close_store
from app.core.errors import AppError, install_error_handlers
from app.core.events import get_bus
from app.core.settings import WEB_OUT_DIR, Settings, get_settings

logger = logging.getLogger(__name__)

_QUIET_LOGGERS = ("httpx", "httpx2", "httpcore", "openai", "urllib3", "websockets", "mcp")


def configure_logging() -> None:
    """Give the root logger a handler before gaik does (it calls `basicConfig` at import), and
    keep HTTP client libraries from logging every request line."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


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
        return {"status": "ok", "features": dict(app.state.features)}

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

    # Added last runs first: (dev CORS) -> access rules -> mounted ASGI apps -> routes.
    app.add_middleware(lifecycle.MountedApps)
    install_auth(app, settings)
    return app


app = create_app()
