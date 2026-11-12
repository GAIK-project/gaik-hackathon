"""Access control (docs/ARCHITECTURE.md sections 6 and 15.2) as one ASGI middleware.

- Open, exactly: `GET /api/health` and `GET|POST /api/whatsapp/webhook`.
- Two bearer keys, each good only for its own routes. `HACK_BRIDGE_KEY`: the bridge's
  WhatsApp session and long poll, the intake endpoints, approving and correcting a report,
  reading one report and its files, and the call WebSocket. `HACK_MCP_KEY`: `/mcp`. A request
  that carries a bearer token is judged by that token alone: a wrong key, or the right key on
  another route, is a `401`.
- Everything else, static files included, needs the signed session cookie: `__Host-gh_session`
  on a public deployment, `gh_session` in local development (`PUBLIC_BASE_URL` on
  `http://localhost` or `http://127.0.0.1`). A `GET` with valid Basic credentials passes and
  gets the cookie; Basic credentials on any other method are ignored. Anything else gets
  `401`, with a Basic challenge when the request was a `GET`.
- A cookie-authenticated request that is not `GET` or `HEAD`, and every cookie-authenticated
  WebSocket handshake, must carry an `Origin` equal to `PUBLIC_BASE_URL` (with `HACK_DEV=1`
  also `http://localhost:3000`): else `403`, or close code 4403. Bearer requests are exempt.
- A refused WebSocket handshake is accepted and closed at once (4401 without valid
  credentials, 4403 for a foreign origin), so the browser can read the code.

What let a request in is in `scope["state"]`, read as `request.state` or `websocket.state`:
`auth` (`open`, `bearer`, `cookie`, `basic` or `disabled`), `key` (`bridge`, `mcp` or `None`)
and `session` (an opaque id of the session cookie, or `None`).

    @router.post("/api/whatsapp/session", dependencies=[Depends(require_key("bridge"))])

`require_key` is for routes only a key holder may call: a browser session gets `403` there.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import re
import secrets
import time
from collections.abc import Callable
from typing import Literal

from fastapi import FastAPI
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import AppError, error_response, status_error
from app.core.settings import DEV_ORIGIN, Settings, origin_of

logger = logging.getLogger(__name__)

COOKIE_NAME = "gh_session"  # local development
HOST_COOKIE_NAME = "__Host-gh_session"  # everywhere else: Secure, Path=/, no Domain
COOKIE_MAX_AGE = 7 * 24 * 60 * 60
REALM = "GAIK mini-hackathon"
WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_FORBIDDEN = 4403

KeyScope = Literal["bridge", "mcp"]

_OPEN_PATHS: dict[str, frozenset[str]] = {
    "/api/health": frozenset({"GET"}),
    "/api/whatsapp/webhook": frozenset({"GET", "POST"}),
}
_SAFE_METHODS = frozenset({"GET", "HEAD"})

_ANY_METHOD = None
# (methods, path) the bridge key is accepted on; the path must match in full.
_BRIDGE_ROUTES: tuple[tuple[frozenset[str] | None, re.Pattern[str]], ...] = tuple(
    (methods, re.compile(pattern))
    for methods, pattern in (
        (_ANY_METHOD, r"/api/whatsapp/session"),
        (_ANY_METHOD, r"/api/whatsapp/pending"),
        (_ANY_METHOD, r"/api/intake/.+"),
        (frozenset({"POST"}), r"/api/reports/[^/]+/approve"),
        (frozenset({"POST"}), r"/api/reports/[^/]+/correct"),
        (frozenset({"GET"}), r"/api/reports/(?!stream$)[^/]+"),
        (frozenset({"GET"}), r"/api/reports/[^/]+/files/.+"),
        (frozenset({"GET"}), r"/api/files/.+"),
    )
)
_BRIDGE_WEBSOCKETS = frozenset({"/api/call/ws"})
_MCP_PATH = re.compile(r"/mcp(/.*)?")


def _equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


# session cookie -----------------------------------------------------------------------------


def cookie_name(settings: Settings) -> str:
    """`gh_session` in local development, `__Host-gh_session` on a public deployment."""
    return COOKIE_NAME if settings.is_local else HOST_COOKIE_NAME


def sign_session(secret: str, *, now: float | None = None, max_age: int = COOKIE_MAX_AGE) -> str:
    """A cookie value `<expires>.<nonce>.<hmac>`; it names no user, there are no accounts."""
    expires = int((time.time() if now is None else now) + max_age)
    payload = f"{expires}.{secrets.token_hex(8)}"
    return f"{payload}.{_signature(secret, payload)}"


def verify_session(secret: str, value: str | None, *, now: float | None = None) -> bool:
    return read_session(secret, value, now=now) is not None


def read_session(secret: str, value: str | None, *, now: float | None = None) -> str | None:
    """The session id (the nonce) of a valid, unexpired cookie value, else `None`."""
    if not secret or not value:
        return None
    payload, _, signature = value.rpartition(".")
    if not payload or not _equal(signature, _signature(secret, payload)):
        return None
    expires, _, nonce = payload.partition(".")
    if not expires.isdigit() or int(expires) <= (time.time() if now is None else now):
        return None
    return nonce or None


def _signature(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def session_cookie_header(settings: Settings, value: str | None = None) -> str:
    """The `Set-Cookie` value for a session (a fresh one when `value` is not given)."""
    parts = [
        f"{cookie_name(settings)}={value or sign_session(settings.hack_session_secret)}",
        "Path=/",
        f"Max-Age={COOKIE_MAX_AGE}",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if not settings.is_local:
        parts.append("Secure")
    return "; ".join(parts)


def _cookie_value(headers: Headers, name: str) -> str | None:
    """One cookie from the request. Parsed by hand: `SimpleCookie` gives up on the whole
    header when another app on the same host has set a cookie it does not like."""
    for raw in headers.getlist("cookie"):
        for part in raw.split(";"):
            key, separator, value = part.strip().partition("=")
            if separator and key == name:
                return value.strip().strip('"')
    return None


def _session(headers: Headers, settings: Settings) -> str | None:
    value = _cookie_value(headers, cookie_name(settings))
    return read_session(settings.hack_session_secret, value)


# credentials in the Authorization header ----------------------------------------------------


def _bearer_token(headers: Headers) -> str | None:
    """The bearer token of the request, or `None` when it carries none."""
    scheme, _, token = headers.get("authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else None


def _key_of(token: str, settings: Settings) -> KeyScope | None:
    """Which key a token is. Both comparisons always run."""
    is_bridge = bool(settings.hack_bridge_key) and _equal(token, settings.hack_bridge_key)
    is_mcp = bool(settings.hack_mcp_key) and _equal(token, settings.hack_mcp_key)
    if is_bridge:
        return "bridge"
    return "mcp" if is_mcp else None


def _plain(path: str) -> bool:
    """No `.` or `..` segment, no backslash: the path means what its pattern says."""
    return "\\" not in path and not any(part in (".", "..") for part in path.split("/"))


def key_allowed(key: KeyScope, kind: str, method: str, path: str) -> bool:
    """Whether a key may be used on this route. `kind` is the ASGI scope type."""
    if not _plain(path):
        return False
    if key == "mcp":
        return kind == "http" and _MCP_PATH.fullmatch(path) is not None
    if kind == "websocket":
        return path in _BRIDGE_WEBSOCKETS
    return any(
        (methods is None or method in methods) and pattern.fullmatch(path)
        for methods, pattern in _BRIDGE_ROUTES
    )


def _has_basic(headers: Headers, settings: Settings) -> bool:
    scheme, _, encoded = headers.get("authorization", "").partition(" ")
    if scheme.lower() != "basic" or not settings.hack_basic_user:
        return False
    if not settings.hack_basic_password:
        return False
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    user, separator, password = decoded.partition(":")
    if not separator:
        return False
    # Both comparisons always run, so timing does not tell which half was wrong.
    user_ok = _equal(user, settings.hack_basic_user)
    password_ok = _equal(password, settings.hack_basic_password)
    return user_ok and password_ok


def _origin_allowed(headers: Headers, settings: Settings) -> bool:
    origin = origin_of(headers.get("origin", ""))
    return bool(origin) and origin in settings.allowed_origins


# middleware ---------------------------------------------------------------------------------


class AuthMiddleware:
    """Pure ASGI middleware: it never buffers a body, so SSE and WebSocket pass untouched."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        state.update(auth=None, key=None, session=None)
        if self.settings.hack_auth_disabled:
            state["auth"] = "disabled"
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]
        headers = Headers(scope=scope)

        if scope["type"] == "websocket":
            await self._websocket(scope, receive, send, path, headers)
            return

        method: str = scope["method"]
        token = _bearer_token(headers)
        if method in _OPEN_PATHS.get(path, ()):
            state["auth"] = "open"
        elif token is not None:
            key = _key_of(token, self.settings)
            if key is None or not key_allowed(key, "http", method, path):
                await self._refuse(scope, receive, send, 401, challenge="Bearer")
                return
            state.update(auth="bearer", key=key)
        elif session := _session(headers, self.settings):
            if method not in _SAFE_METHODS and not _origin_allowed(headers, self.settings):
                logger.warning("%s %s refused: foreign or missing Origin", method, path)
                await self._refuse(scope, receive, send, 403)
                return
            state.update(auth="cookie", session=session)
        elif method == "GET" and _has_basic(headers, self.settings):
            state["auth"] = "basic"
            send = self._with_session_cookie(send, state)
        else:
            # The Basic challenge goes out only where Basic is honoured. On a write it would
            # make the browser ask for the password and retry in a loop that cannot succeed.
            challenge = f'Basic realm="{REALM}"' if method == "GET" else None
            await self._refuse(scope, receive, send, 401, challenge=challenge)
            return
        await self.app(scope, receive, send)

    async def _websocket(
        self, scope: Scope, receive: Receive, send: Send, path: str, headers: Headers
    ) -> None:
        state = scope["state"]
        token = _bearer_token(headers)
        code = WS_CLOSE_UNAUTHORIZED
        if token is not None:
            key = _key_of(token, self.settings)
            if key is not None and key_allowed(key, "websocket", "GET", path):
                state.update(auth="bearer", key=key)
        elif session := _session(headers, self.settings):
            if _origin_allowed(headers, self.settings):
                state.update(auth="cookie", session=session)
            else:
                logger.warning("WebSocket %s refused: foreign or missing Origin", path)
                code = WS_CLOSE_FORBIDDEN
        if state["auth"] is None:
            # Closing before `accept` would reach the browser as a failed handshake (1006).
            message = await receive()
            if message["type"] == "websocket.connect":
                reason = "forbidden" if code == WS_CLOSE_FORBIDDEN else "unauthorized"
                await send({"type": "websocket.accept"})
                await send({"type": "websocket.close", "code": code, "reason": reason})
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _refuse(
        scope: Scope, receive: Receive, send: Send, status: int, *, challenge: str | None = None
    ) -> None:
        code, message = status_error(status)
        if status == 403:
            code, message = "bad_origin", "Pyyntö tuli vieraalta sivustolta."
        headers = {"WWW-Authenticate": challenge} if challenge else None
        await error_response(code, message, status, headers=headers)(scope, receive, send)

    def _with_session_cookie(self, send: Send, state: dict[str, object]) -> Send:
        settings = self.settings
        if not settings.hack_session_secret:
            return send  # no secret, no cookie: Basic keeps working request by request
        value = sign_session(settings.hack_session_secret)
        state["session"] = read_session(settings.hack_session_secret, value)
        cookie = session_cookie_header(settings, value)

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append("set-cookie", cookie)
            await send(message)

        return send_with_cookie


def install_auth(app: FastAPI, settings: Settings) -> None:
    """Add the access rules, and dev CORS around them when `HACK_DEV=1`.

    Call this after every other `add_middleware`: the middleware added last runs first, and
    CORS has to answer a preflight (which carries no credentials) before auth sees it.
    `main.create_app` has already refused settings that must not serve (section 15.2).
    """
    app.add_middleware(AuthMiddleware, settings=settings)
    if settings.hack_dev:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[DEV_ORIGIN],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    if settings.hack_auth_disabled:
        logger.warning("HACK_AUTH_DISABLED is on: every request is let in. Local development only.")


# dependencies and helpers for routes --------------------------------------------------------


def _state(connection: HTTPConnection) -> dict[str, object]:
    return connection.scope.get("state") or {}


def auth_kind(connection: HTTPConnection) -> str | None:
    """How the request or WebSocket got in: `open`, `bearer`, `cookie`, `basic`, `disabled`."""
    kind = _state(connection).get("auth")
    return kind if isinstance(kind, str) else None


def key_scope(connection: HTTPConnection) -> KeyScope | None:
    """The bearer key the request or WebSocket came in with: `bridge`, `mcp` or `None`."""
    key = _state(connection).get("key")
    return key if key in ("bridge", "mcp") else None  # type: ignore[return-value]


def require_key(scope: KeyScope) -> Callable[[HTTPConnection], None]:
    """A dependency for routes only the holder of one key may call:
    `Depends(require_key("bridge"))`. With access control switched off it lets everyone in."""
    if scope not in ("bridge", "mcp"):
        raise ValueError("scope is bridge or mcp")

    def dependency(connection: HTTPConnection) -> None:
        if auth_kind(connection) == "disabled" or key_scope(connection) == scope:
            return
        raise AppError("forbidden", "Sinulla ei ole oikeutta tähän.", 403)

    return dependency


# The name the first foundation used for the bridge-only dependency.
require_bearer = require_key("bridge")
