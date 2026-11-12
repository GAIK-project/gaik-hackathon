"""The MCP server at `/mcp`: Streamable HTTP, stateless, JSON answers (section 15.10).

Importing this module registers the server with `core/lifecycle`:

    lifecycle.register("mcp", context=_running)     # enters mcp.session_manager.run()
    lifecycle.mount_asgi("/mcp", _MountedMcp())     # behind the access rules: HACK_MCP_KEY only

`mcp` (the `MCPServer` with every tool of `tools.SPECS`) lives for the whole process. Its
Streamable HTTP app and session manager are made anew at each startup, because a session
manager runs only once and the allowed hosts come from the settings of that start
(`PUBLIC_BASE_URL`). There is no gate here: `core/auth` refuses `/mcp` without the MCP key.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core import lifecycle, limits
from app.core.errors import error_response
from app.core.settings import get_settings
from app.mcp import tools

logger = logging.getLogger(__name__)

MOUNT_PATH = "/mcp"
INSTRUCTIONS = (
    "GAIK toolkit components as tools: read documents, pull structured fields out of text, "
    "transcribe speech, classify text, search and apply the EU AI Act, and read or create "
    "field reports. All content in this demo is synthetic. Do not share personal data. "
    "Nothing here sends mail or approves a report; a person does that in the app."
)


def build_server() -> MCPServer:
    server = MCPServer("gaik-hackathon", title="GAIK mini-hackathon", instructions=INSTRUCTIONS)
    for spec in tools.SPECS.values():
        server.add_tool(spec.run, name=spec.name, description=spec.description)
    return server


mcp = build_server()


def allowed_hosts() -> list[str]:
    """Host headers the transport answers: local ones and the public host of this start."""
    hosts = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"]
    public = get_settings().public_host
    if public and public not in ("127.0.0.1", "localhost"):
        hosts += [public, f"{public}:*"]
    return hosts


def _transport_security() -> TransportSecuritySettings:
    settings = get_settings()
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts(),
        allowed_origins=list(settings.allowed_origins),
    )


_asgi: ASGIApp | None = None


@asynccontextmanager
async def _running() -> AsyncIterator[None]:
    """One start of the app: a fresh Streamable HTTP app with its session manager running."""
    global _asgi
    app = mcp.streamable_http_app(
        streamable_http_path=MOUNT_PATH,
        stateless_http=True,
        json_response=True,
        max_request_body_size=limits.body_limit_for(MOUNT_PATH),
        transport_security=_transport_security(),
    )
    async with mcp.session_manager.run():
        _asgi = app
        try:
            yield
        finally:
            _asgi = None


class _MountedMcp:
    """What `lifecycle.mount_asgi` serves: the Streamable HTTP app of the current start."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if _asgi is None or scope["type"] != "http":
            if scope["type"] == "http":
                response = error_response("unavailable", "The MCP server is not running.", 503)
                await response(scope, receive, send)
            return
        await _asgi(scope, receive, send)


lifecycle.register("mcp", context=_running)
lifecycle.mount_asgi(MOUNT_PATH, _MountedMcp())


async def tool_listing() -> list[dict[str, Any]]:
    """Every tool as the server announces it: name, description and argument schema."""
    listed = await mcp.list_tools()
    return [
        {
            "name": item.name,
            "description": item.description or "",
            "arguments": item.input_schema,
            "model_backed": tools.SPECS[item.name].model_backed,
            "sample": tools.SPECS[item.name].sample_shown,
        }
        for item in listed
    ]
