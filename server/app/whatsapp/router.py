"""WhatsApp endpoints (docs/ARCHITECTURE.md sections 9 and 15.11).

Meta calls the open webhook; the API stores the raw delivery and answers at once. The bridge
on the owner's machine, the only holder of the Meta keys, starts a session with the bridge
key, long-polls the deliveries and does everything else.

| Method and path               | Access     | Behaviour                                         |
| ----------------------------- | ---------- | ------------------------------------------------- |
| `GET /api/whatsapp/webhook`   | open       | answers `hub.challenge` for the session's token   |
| `POST /api/whatsapp/webhook`  | open       | stores body and signature header, always 200      |
| `POST /api/whatsapp/session`  | bridge key | `{verify_token}` -> `{callback_url}`              |
| `GET /api/whatsapp/pending`   | bridge key | long poll: `{items}`, each removed once returned  |
| `DELETE /api/whatsapp/session`| bridge key | ends the session, clears the queue                |
| `GET /api/whatsapp/status`    | session    | `{active, last_poll_at, queued}`                  |

The webhook never parses a body and nothing about a delivery is logged: the sender's phone
number exists here only inside the queued bytes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.core import lifecycle
from app.core.auth import require_key
from app.core.errors import AppError
from app.core.settings import get_settings
from app.whatsapp.queue import MAX_WAIT_S, NoSession, get_hub

router = APIRouter(tags=["whatsapp"])

WEBHOOK_PATH = "/api/whatsapp/webhook"
_SIGNATURE_HEADER = "x-hub-signature-256"

lifecycle.register("whatsapp", startup=get_hub().open, shutdown=get_hub().close)


class SessionRequest(BaseModel):
    verify_token: str = Field(min_length=16, max_length=200, pattern=r"^[A-Za-z0-9_\-]+$")


@router.get(WEBHOOK_PATH, include_in_schema=False)
async def webhook_handshake(request: Request) -> PlainTextResponse:
    params = request.query_params
    if not get_hub().challenge_ok(params.get("hub.mode"), params.get("hub.verify_token")):
        return PlainTextResponse("Forbidden", status_code=403)
    return PlainTextResponse(params.get("hub.challenge", ""))


@router.post(WEBHOOK_PATH, include_in_schema=False)
async def webhook_delivery(request: Request) -> PlainTextResponse:
    # Same answer whether the delivery was kept or dropped: the caller learns nothing.
    get_hub().store(await request.body(), request.headers.get(_SIGNATURE_HEADER))
    return PlainTextResponse("ok")


@router.post("/api/whatsapp/session", dependencies=[Depends(require_key("bridge"))])
async def start_session(body: SessionRequest) -> dict[str, str]:
    get_hub().start(body.verify_token)
    base = get_settings().public_base_url.rstrip("/")
    return {"callback_url": f"{base}{WEBHOOK_PATH}"}


@router.delete("/api/whatsapp/session", dependencies=[Depends(require_key("bridge"))])
async def end_session() -> dict[str, bool]:
    get_hub().end()
    return {"active": False}


@router.get("/api/whatsapp/pending", dependencies=[Depends(require_key("bridge"))])
async def pending(wait: float = Query(25.0, ge=0, le=MAX_WAIT_S)) -> dict[str, list]:
    try:
        items = await get_hub().take(wait)
    except NoSession:
        raise AppError("no_session", "No bridge session is active.", 409) from None
    return {"items": [item.as_json() for item in items]}


@router.get("/api/whatsapp/status")
async def status() -> dict[str, object]:
    return get_hub().status()
