"""`WS /api/call/ws`: the voice call (docs/ARCHITECTURE.md sections 8.2, 15.3 and 15.7).

Access is decided by `core/auth` before this route runs (session cookie with the app's Origin,
or the bridge key). The two dependencies exist so tests can replace them:

    app.dependency_overrides[reports_service] = lambda: fake_reports
    app.dependency_overrides[realtime_connect] = lambda: fake_connect
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, WebSocket

from app.voice.relay import CallRelay, Connect, open_realtime

router = APIRouter()


def reports_service() -> Any:
    """`app.reports.service`, imported when a call starts (it is another feature's module)."""
    from app.reports import service

    return service


def realtime_connect() -> Connect:
    return open_realtime


@router.websocket("/api/call/ws")
async def call_ws(
    websocket: WebSocket,
    service: Any = Depends(reports_service),  # noqa: B008
    connect: Connect = Depends(realtime_connect),  # noqa: B008
) -> None:
    await CallRelay(websocket, service=service, connect=connect).run()
