"""Access control (docs/ARCHITECTURE.md section 6) as one ASGI middleware.

- `GET /api/health` and `GET|POST /api/whatsapp/webhook` are open.
- `/mcp` and `/api/**` accept `Authorization: Bearer <HACK_API_KEY>`.
- Everything else, static files included, needs the signed `gh_session` cookie. A request with
  valid Basic credentials passes and gets the cookie; anything else gets `401` with a Basic
  challenge.
- WebSocket handshakes use the cookie (or the bearer key under `/api/`); a refused handshake is
  accepted and closed at once with code 4401, so the browser can read the code.

How a request got in is stored in `scope["state"]["auth"]` (`open`, `bearer`, `cookie`,
`basic` or `disabled`); `require_bearer` is a dependency for routes only the bridge may call.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import secrets
import time
from http.cookies import SimpleCookie

from fastapi import FastAPI, Request
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import AppError, error_response, status_error
from app.core.settings import Settings

logger = logging.getLogger(__name__)

COOKIE_NAME = "gh_session"
COOKIE_MAX_AGE = 7 * 24 * 60 * 60
REALM = "GAIK mini-hackathon"
WS_CLOSE_UNAUTHORIZED = 4401
DEV_ORIGIN = "http://localhost:3000"

_OPEN_PATHS: dict[str, frozenset[str]] = {
    "/api/health": frozenset({"GET", "HEAD"}),
    "/api/whatsapp/webhook": frozenset({"GET", "POST"}),
}


def _equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def sign_session(secret: str, *, now: float | None = None, max_age: int = COOKIE_MAX_AGE) -> str:
    """A cookie value `<expires>.<nonce>.<hmac>`; it names no user, there are no accounts."""
    expires = int((time.time() if now is None else now) + max_age)
    payload = f"{expires}.{secrets.token_hex(8)}"
    return f"{payload}.{_signature(secret, payload)}"


def verify_session(secret: str, value: str | None, *, now: float | None = None) -> bool:
    if not secret or not value:
        return False
    payload, _, signature = value.rpartition(".")
    if not payload or not _equal(signature, _signature(secret, payload)):
        return False
    expires = payload.split(".", 1)[0]
    return expires.isdigit() and int(expires) > (time.time() if now is None else now)


def _signature(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def session_cookie_header(settings: Settings) -> str:
    """The `Set-Cookie` value for a fresh session."""
    parts = [
        f"{COOKIE_NAME}={sign_session(settings.hack_session_secret)}",
        "Path=/",
        f"Max-Age={COOKIE_MAX_AGE}",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if not settings.hack_dev:
        parts.append("Secure")
    return "; ".join(parts)


def _is_api_path(path: str) -> bool:
    return path == "/mcp" or path.startswith(("/mcp/", "/api/"))


def _has_bearer(headers: Headers, settings: Settings) -> bool:
    scheme, _, token = headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not settings.hack_api_key:
        return False
    return _equal(token.strip(), settings.hack_api_key)


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


def _has_cookie(headers: Headers, settings: Settings) -> bool:
    raw = headers.get("cookie")
    if not raw:
        return False
    try:
        jar: SimpleCookie = SimpleCookie(raw)
    except Exception:  # noqa: BLE001  (a malformed Cookie header is simply not a session)
        return False
    morsel = jar.get(COOKIE_NAME)
    return verify_session(settings.hack_session_secret, morsel.value if morsel else None)


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
        if self.settings.hack_auth_disabled:
            state["auth"] = "disabled"
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]
        headers = Headers(scope=scope)

        if scope["type"] == "websocket":
            await self._websocket(scope, receive, send, path, headers)
            return

        if scope["method"] in _OPEN_PATHS.get(path, ()):
            state["auth"] = "open"
        elif _is_api_path(path) and _has_bearer(headers, self.settings):
            state["auth"] = "bearer"
        elif _has_cookie(headers, self.settings):
            state["auth"] = "cookie"
        elif _has_basic(headers, self.settings):
            state["auth"] = "basic"
            send = self._with_session_cookie(send)
        else:
            code, message = status_error(401)
            response = error_response(
                code, message, 401, headers={"WWW-Authenticate": f'Basic realm="{REALM}"'}
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _websocket(
        self, scope: Scope, receive: Receive, send: Send, path: str, headers: Headers
    ) -> None:
        state = scope["state"]
        if _has_cookie(headers, self.settings):
            state["auth"] = "cookie"
        elif _is_api_path(path) and _has_bearer(headers, self.settings):
            state["auth"] = "bearer"
        else:
            # Closing before `accept` would reach the browser as a failed handshake (1006).
            message = await receive()
            if message["type"] == "websocket.connect":
                await send({"type": "websocket.accept"})
                await send(
                    {
                        "type": "websocket.close",
                        "code": WS_CLOSE_UNAUTHORIZED,
                        "reason": "unauthorized",
                    }
                )
            return
        await self.app(scope, receive, send)

    def _with_session_cookie(self, send: Send) -> Send:
        settings = self.settings
        if not settings.hack_session_secret:
            return send  # no secret, no cookie: Basic keeps working request by request

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append("set-cookie", session_cookie_header(settings))
            await send(message)

        return send_with_cookie


def install_auth(app: FastAPI, settings: Settings) -> None:
    """Add the access rules, and dev CORS around them when `HACK_DEV=1`.

    Call this after every other `add_middleware`: the middleware added last runs first, and
    CORS has to answer a preflight (which carries no credentials) before auth sees it.
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
        return
    missing = settings.missing(
        "hack_basic_user", "hack_basic_password", "hack_session_secret", "hack_api_key"
    )
    if missing:
        logger.warning("Access settings without a value: %s", ", ".join(missing))


def require_bearer(request: Request) -> None:
    """Dependency for routes that only a holder of `HACK_API_KEY` may call (the bridge)."""
    if getattr(request.state, "auth", None) not in ("bearer", "disabled"):
        raise AppError("forbidden", "Sinulla ei ole oikeutta tähän.", 403)
