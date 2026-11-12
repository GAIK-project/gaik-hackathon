"""The API side: handshake, storing, the session, the long poll and who may call what."""

from __future__ import annotations

import asyncio
import base64

import anyio
import pytest

from app.whatsapp import queue
from app.whatsapp.queue import BridgeHub, NoSession, get_hub

TOKEN = "verify-token-0123456789abcdef"
SIGNATURE = "sha256=" + "ab" * 32
WEBHOOK = "/api/whatsapp/webhook"
BODY = b'{"object": "whatsapp_business_account",\n  "entry": [ ]  }\xc3\xa4 '


def start(bridge_client) -> dict:
    response = bridge_client.post("/api/whatsapp/session", json={"verify_token": TOKEN})
    assert response.status_code == 200
    return response.json()


def deliver(anon_client, body: bytes = BODY, signature: str | None = SIGNATURE):
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return anon_client.post(WEBHOOK, content=body, headers=headers)


def test_session_returns_the_public_callback(bridge_client):
    assert start(bridge_client) == {
        "callback_url": "https://gaik-hackathon.example/api/whatsapp/webhook"
    }


def test_handshake_answers_only_the_live_token(bridge_client, anon_client):
    query = {"hub.mode": "subscribe", "hub.verify_token": TOKEN, "hub.challenge": "4242"}
    assert anon_client.get(WEBHOOK, params=query).status_code == 403  # no session yet
    start(bridge_client)
    response = anon_client.get(WEBHOOK, params=query)
    assert response.status_code == 200
    assert response.text == "4242"
    assert response.headers["content-type"].startswith("text/plain")
    assert anon_client.get(WEBHOOK, params={**query, "hub.verify_token": "x"}).status_code == 403
    assert anon_client.get(WEBHOOK, params={**query, "hub.mode": "other"}).status_code == 403
    bridge_client.delete("/api/whatsapp/session")
    assert anon_client.get(WEBHOOK, params=query).status_code == 403


def test_delivery_without_a_session_is_dropped(bridge_client, anon_client, client):
    assert deliver(anon_client).status_code == 200
    assert client.get("/api/whatsapp/status").json() == {
        "active": False,
        "last_poll_at": None,
        "queued": 0,
    }
    assert bridge_client.get("/api/whatsapp/pending?wait=0").status_code == 409


def test_pending_returns_the_exact_bytes_once(bridge_client, anon_client, client):
    start(bridge_client)
    assert deliver(anon_client).status_code == 200
    assert client.get("/api/whatsapp/status").json()["queued"] == 1
    items = bridge_client.get("/api/whatsapp/pending?wait=0").json()["items"]
    assert len(items) == 1
    assert set(items[0]) == {"id", "received_at", "signature", "body_b64"}
    assert base64.b64decode(items[0]["body_b64"]) == BODY
    assert items[0]["signature"] == SIGNATURE
    assert items[0]["received_at"].endswith("Z")
    assert bridge_client.get("/api/whatsapp/pending?wait=0").json() == {"items": []}
    status = client.get("/api/whatsapp/status").json()
    assert status["active"] is True and status["queued"] == 0 and status["last_poll_at"]


@pytest.mark.parametrize(
    "signature",
    [None, "", "sha256=" + "AB" * 32, "sha256=" + "ab" * 31, "sha1=" + "ab" * 32, "ab" * 32],
)
def test_malformed_signature_header_is_dropped(bridge_client, anon_client, signature):
    start(bridge_client)
    assert deliver(anon_client, signature=signature).status_code == 200
    assert bridge_client.get("/api/whatsapp/pending?wait=0").json() == {"items": []}


def test_oversized_delivery_is_dropped(bridge_client, anon_client):
    start(bridge_client)
    assert deliver(anon_client, body=b"x" * (queue.MAX_ITEM_BYTES + 1)).status_code == 200
    assert deliver(anon_client, body=b"x" * queue.MAX_ITEM_BYTES).status_code == 200
    assert len(bridge_client.get("/api/whatsapp/pending?wait=0").json()["items"]) == 1


def test_ending_or_replacing_the_session_clears_the_queue(bridge_client, anon_client):
    start(bridge_client)
    deliver(anon_client)
    start(bridge_client)  # a new session replaces the old one
    assert bridge_client.get("/api/whatsapp/pending?wait=0").json() == {"items": []}
    deliver(anon_client)
    assert bridge_client.delete("/api/whatsapp/session").json() == {"active": False}
    assert get_hub().status()["queued"] == 0
    assert bridge_client.get("/api/whatsapp/pending?wait=0").json()["error"]["code"] == "no_session"


def test_only_the_bridge_key_may_use_the_session_routes(client, anon_client, mcp_client):
    body = {"verify_token": TOKEN}
    assert client.post("/api/whatsapp/session", json=body).status_code == 403
    assert anon_client.post("/api/whatsapp/session", json=body).status_code == 401
    assert mcp_client.post("/api/whatsapp/session", json=body).status_code == 401
    assert client.get("/api/whatsapp/pending?wait=0").status_code == 403
    assert anon_client.get("/api/whatsapp/pending?wait=0").status_code == 401
    assert client.delete("/api/whatsapp/session").status_code == 403
    assert anon_client.get("/api/whatsapp/status").status_code == 401
    assert get_hub().active() is False


def test_verify_token_must_be_well_formed(bridge_client):
    for bad in ("short", "has space 0123456789abcdef", "x" * 201):
        response = bridge_client.post("/api/whatsapp/session", json={"verify_token": bad})
        assert response.status_code == 422
    assert get_hub().active() is False


def test_whatsapp_is_a_listed_feature(anon_client):
    assert anon_client.get("/api/health").json()["features"]["whatsapp"] is True


# the hub itself --------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_queue_is_bounded_and_drops_the_oldest():
    hub = BridgeHub()
    hub.start(TOKEN)
    for number in range(queue.MAX_ITEMS + 5):
        assert hub.store(b"body-%d" % number, SIGNATURE)
    assert hub.status()["queued"] == queue.MAX_ITEMS
    items, _ = hub._take_now(None)
    assert items[0].body == b"body-5"
    assert items[-1].body == b"body-%d" % (queue.MAX_ITEMS + 4)


def test_session_expires_after_twelve_hours_without_a_poll():
    clock = Clock()
    hub = BridgeHub(clock)
    hub.start(TOKEN)
    hub.store(b"one", SIGNATURE)
    clock.now += queue.SESSION_TTL_S - 1
    hub._take_now(None)  # a poll keeps it alive
    hub.store(b"two", SIGNATURE)
    clock.now += queue.SESSION_TTL_S + 1
    assert hub.status() == {"active": False, "last_poll_at": None, "queued": 0}
    assert hub.store(b"three", SIGNATURE) is False
    assert hub.challenge_ok("subscribe", TOKEN) is False
    with pytest.raises(NoSession):
        hub._take_now(None)


async def test_long_poll_wakes_for_a_delivery_and_ends_with_the_session():
    hub = BridgeHub()
    hub.start(TOKEN)

    async def later(action) -> None:
        await anyio.sleep(0.3)
        action()

    task = asyncio.create_task(later(lambda: hub.store(b"late", SIGNATURE)))
    with anyio.fail_after(5):
        items = await hub.take(wait_s=10)
    await task
    assert [item.body for item in items] == [b"late"]

    assert await hub.take(wait_s=0) == []
    task = asyncio.create_task(later(hub.end))
    with pytest.raises(NoSession):
        with anyio.fail_after(5):
            await hub.take(wait_s=10)
    await task
