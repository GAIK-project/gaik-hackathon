"""The MCP page's API (section 8.5). Importing this module also registers the MCP server.

    GET  /api/mcp/info              the public URL, the tools with argument schemas, client snippets
    GET  /api/mcp/activity          the newest tool calls
    GET  /api/mcp/activity/stream   SSE: `call` when a tool call starts and when it ends
    POST /api/mcp/try               {"tool", "arguments"?} runs one tool on the server

`try` without `arguments` uses the tool's small built-in sample. It answers
`{tool, ok: true, ms, arguments, result}`; a failure is the usual error envelope. The key is
in none of these answers: the snippets carry a placeholder.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

from app.core import limits
from app.core.errors import AppError
from app.core.events import get_bus, sse_response
from app.core.settings import get_settings
from app.mcp import activity, server, tools

logger = logging.getLogger(__name__)

router = APIRouter()

KEY_PLACEHOLDER = "<YOUR_MCP_KEY>"
KEY_ENV = "GAIK_MCP_KEY"
SERVER_NAME = "gaik"
TRY_CLIENT = "Try it (this page)"


def _public_url(request: Request) -> str:
    base = get_settings().public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    return f"{base}{server.MOUNT_PATH}"


def client_snippets(url: str) -> list[dict[str, str]]:
    """Paste-ready ways to add the server, with a placeholder where the key goes."""
    mcp_json = {
        "mcpServers": {
            SERVER_NAME: {
                "type": "http",
                "url": url,
                "headers": {"Authorization": f"Bearer ${{{KEY_ENV}}}"},
            }
        }
    }
    cursor_json = {
        "mcpServers": {
            SERVER_NAME: {"url": url, "headers": {"Authorization": f"Bearer ${{env:{KEY_ENV}}}"}}
        }
    }
    return [
        {
            "id": "claude-code",
            "title": "Claude Code",
            "language": "bash",
            "note": "Run in a terminal, then start Claude Code and type /mcp to check.",
            "snippet": (
                f"claude mcp add --transport http {SERVER_NAME} {url} "
                f'--header "Authorization: Bearer {KEY_PLACEHOLDER}"'
            ),
        },
        {
            "id": "mcp-json",
            "title": ".mcp.json (project file)",
            "language": "json",
            "note": f"Put the key in the environment variable {KEY_ENV}; the file holds no key.",
            "snippet": json.dumps(mcp_json, indent=2),
        },
        {
            "id": "claude-desktop",
            "title": "Claude Desktop and claude.ai",
            "language": "text",
            "note": "Works when this server is reachable from the internet.",
            "snippet": (
                "1. Open Customize > Connectors > + Add > Add custom connector.\n"
                f"2. Name: GAIK. URL: {url}\n"
                f"3. Under Request headers add  Authorization: Bearer {KEY_PLACEHOLDER}\n"
                "4. Save, then switch the connector on in a chat."
            ),
        },
        {
            "id": "codex",
            "title": "Codex",
            "language": "toml",
            "note": f"Add to ~/.codex/config.toml and put the key in {KEY_ENV}.",
            "snippet": (
                f'[mcp_servers.{SERVER_NAME}]\nurl = "{url}"\nbearer_token_env_var = "{KEY_ENV}"'
            ),
        },
        {
            "id": "cursor",
            "title": "Cursor",
            "language": "json",
            "note": f"Save as .cursor/mcp.json and put the key in {KEY_ENV}.",
            "snippet": json.dumps(cursor_json, indent=2),
        },
    ]


@router.get("/api/mcp/info")
async def info(request: Request) -> dict[str, Any]:
    url = _public_url(request)
    return {
        "url": url,
        "transport": "Streamable HTTP",
        "auth": f"Authorization: Bearer {KEY_PLACEHOLDER}",
        "notices": [tools.NOTICE_PERSONAL],
        "tools": await server.tool_listing(),
        "clients": client_snippets(url),
    }


@router.get("/api/mcp/activity")
async def recent_activity() -> list[dict[str, Any]]:
    return activity.recent()


@router.get("/api/mcp/activity/stream")
async def activity_stream() -> StreamingResponse:
    return sse_response(get_bus().subscribe(activity.TOPIC))


class TryBody(BaseModel):
    tool: str
    arguments: dict[str, Any] | None = None


def _shown(arguments: dict[str, Any]) -> dict[str, Any]:
    """Arguments for the answer, with file content left out."""
    return {
        key: tools.SAMPLE_PLACEHOLDER if key == "content_base64" else value
        for key, value in arguments.items()
    }


def _tool_error(error: ToolError) -> AppError:
    for candidate in (error, error.__cause__):
        if isinstance(candidate, tools.ToolFailure):
            return AppError(candidate.code, candidate.message, candidate.status)
    # The SDK's own refusal: the arguments did not fit the tool's schema.
    return AppError("invalid_arguments", "The arguments are not valid for this tool.", 400)


@router.post("/api/mcp/try")
async def try_tool(body: TryBody, request: Request) -> dict[str, Any]:
    spec = tools.SPECS.get(body.tool)
    if spec is None:
        raise AppError("unknown_tool", "There is no tool with that name.", 404)
    if spec.model_backed:
        try:
            limits.model_call(request)
        except AppError as error:
            raise tools.in_english(error) from None
    token = tools.calling_as(tools.Caller(client=TRY_CLIENT))
    started = time.perf_counter()
    try:
        arguments = body.arguments or await spec.sample()
        outcome = await server.mcp.call_tool(spec.name, arguments)
    except ToolError as error:
        raise _tool_error(error) from None
    finally:
        tools.reset_caller(token)
    return {
        "tool": spec.name,
        "ok": True,
        "ms": int((time.perf_counter() - started) * 1000),
        "arguments": _shown(arguments),
        "result": outcome.structured_content,
    }
