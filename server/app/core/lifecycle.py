"""What runs around the app: feature routers, startup and shutdown work, raw ASGI apps.

A feature lives in `app/<feature>/` and exposes `router` (a FastAPI `APIRouter` with full
paths such as `/api/reports`) in `app/<feature>/router.py`. The app includes every feature
that imports and starts without the ones that do not, so it runs while features are written.

A feature registers its own startup and shutdown work when its router module is imported:

    from app.core import lifecycle

    lifecycle.register("aiact", startup=load_corpus)                    # sync or async
    lifecycle.register("mcp", context=lambda: mcp.session_manager.run())  # async context
    lifecycle.mount_asgi("/mcp", mcp.streamable_http_app(...))            # raw ASGI at a path

`mount_asgi` serves a raw ASGI app at one exact path, with or without a trailing slash and
without a redirect. It sits behind the access rules, so `/mcp` gets the bearer check of
section 6 for free.
"""

from __future__ import annotations

import importlib
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

FEATURES: tuple[str, ...] = ("reports", "voice", "studio", "aiact", "mcp", "whatsapp")

Hook = Callable[[], Awaitable[Any] | Any]
ContextFactory = Callable[[], AbstractAsyncContextManager[Any]]


@dataclass(frozen=True, slots=True)
class Registration:
    name: str
    startup: Hook | None = None
    shutdown: Hook | None = None
    context: ContextFactory | None = None


_registrations: dict[str, Registration] = {}
_mounts: dict[str, ASGIApp] = {}


def register(
    name: str,
    *,
    startup: Hook | None = None,
    shutdown: Hook | None = None,
    context: ContextFactory | None = None,
) -> None:
    """Register work to run when the app starts and stops.

    `startup` and `shutdown` are plain or async functions without arguments. `context` is a
    function that returns an async context manager; it is entered at startup and left at
    shutdown in the same task (what a task group, such as the MCP session manager, needs).
    Order: registration order at startup, the reverse at shutdown. Registering a name again
    replaces the earlier entry. A feature whose startup fails is reported as `false` in
    `/api/health`; the app still starts.
    """
    _registrations[name] = Registration(name, startup, shutdown, context)


def unregister(name: str) -> None:
    _registrations.pop(name, None)


def registrations() -> list[Registration]:
    return list(_registrations.values())


def mount_asgi(path: str, app: ASGIApp) -> None:
    """Serve a raw ASGI app at exactly `path` (and `path/`), behind the access rules."""
    if not path.startswith("/") or path == "/":
        raise ValueError("mount_asgi needs a path such as /mcp")
    _mounts[path.rstrip("/")] = app


def unmount_asgi(path: str) -> None:
    _mounts.pop(path.rstrip("/"), None)


def mounted_paths() -> list[str]:
    return sorted(_mounts)


class MountedApps:
    """ASGI middleware that hands the paths of `mount_asgi` to their apps."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and _mounts:
            path = scope["path"].rstrip("/")
            target = _mounts.get(path)
            if target is not None:
                await target(dict(scope, path=path, raw_path=path.encode()), receive, send)
                return
        await self.app(scope, receive, send)


def include_features(app: FastAPI, names: tuple[str, ...] = FEATURES) -> dict[str, bool]:
    """Include `app.<feature>.router.router` for every feature that imports.

    Returns `{feature: included}`. A feature that is not written yet is a warning, one that
    fails to import is an error with its traceback; neither stops the app.
    """
    included: dict[str, bool] = {}
    for name in names:
        module_name = f"app.{name}.router"
        included[name] = False
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as error:
            if error.name in (f"app.{name}", module_name):
                logger.warning("Feature %s is not there yet (%s is missing)", name, error.name)
            else:
                logger.error("Feature %s needs a module that is not installed: %s", name, error)
            continue
        except Exception:
            logger.exception("Feature %s failed to import; the app starts without it", name)
            continue
        router = getattr(module, "router", None)
        if not isinstance(router, APIRouter):
            logger.error("Feature %s: %s has no APIRouter named router", name, module_name)
            continue
        app.include_router(router)
        included[name] = True
    return included


async def _call(hook: Hook) -> None:
    result = hook()
    if inspect.isawaitable(result):
        await result


@asynccontextmanager
async def _quiet(name: str, context: AbstractAsyncContextManager[Any]) -> AsyncIterator[None]:
    """Enter and leave a registered context; an error on the way out is logged, not raised."""
    await context.__aenter__()
    try:
        yield
    finally:
        try:
            await context.__aexit__(None, None, None)
        except Exception:
            logger.exception("Shutdown of %s failed", name)


async def _shutdown(name: str, hook: Hook) -> None:
    try:
        await _call(hook)
    except Exception:
        logger.exception("Shutdown of %s failed", name)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """The FastAPI lifespan: runs everything registered, in one task."""
    features: dict[str, bool] = getattr(app.state, "features", {})
    async with AsyncExitStack() as stack:
        for registration in registrations():
            try:
                if registration.context is not None:
                    await stack.enter_async_context(
                        _quiet(registration.name, registration.context())
                    )
                if registration.startup is not None:
                    await _call(registration.startup)
            except Exception:
                logger.exception(
                    "Startup of %s failed; the app starts without it", registration.name
                )
                if registration.name in features:
                    features[registration.name] = False
                continue
            if registration.shutdown is not None:
                stack.push_async_callback(_shutdown, registration.name, registration.shutdown)
        yield
