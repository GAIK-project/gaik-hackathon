"""The bridge (`bridge/main.py`) against a fake Graph server and signed payloads.

One `httpx.MockTransport` plays Meta's Graph API, Meta's media host and the app's report
endpoints, and records every request. All numbers, ids and keys here are made up.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("bridge_main", REPO / "bridge" / "main.py")
bridge_main = importlib.util.module_from_spec(_spec)
sys.modules["bridge_main"] = bridge_main
_spec.loader.exec_module(bridge_main)

TOKEN = "graph-access-token"
APP_SECRET = "graph-app-secret"
BRIDGE_KEY = "test-bridge-key"
API = "https://gaik-hackathon.example"
SENDER = "100000000001"
STRANGER = "100000000002"
PHONE_ID = "100200300"
WABA_ID = "400500600"
APP_ID = "555"
MEDIA_URL = "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1"
OGG = b"OggS" + b"\x00" * 60
REPORT = {
    "id": "rep_abc123",
    "status": "needs_approval",
    "type_title": "Safety observation",
    "headline": "Loose railing on the second floor",
    "filled": 5,
    "total": 6,
    "files": [
        {
            "name": "f_1.docx",
            "kind": "docx",
            "size": 7,
            "url": "/api/reports/rep_abc123/files/f_1.docx",
        }
    ],
}
REPLY = "Safety observation recorded. One field is missing."


class World:
    """Fake Graph, fake media host and fake app in one transport."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.media_url = MEDIA_URL
        self.approvals = 0
        self.app = None  # an ASGI transport for /api/whatsapp/*, when a test wants the real one

    def named(self, method: str, ending: str) -> list[httpx.Request]:
        return [c for c in self.calls if c.method == method and c.url.path.endswith(ending)]

    def sent(self) -> list[dict[str, Any]]:
        return [json.loads(c.content) for c in self.named("POST", f"/{PHONE_ID}/messages")]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.calls.append(request)
        host, path = request.url.host, request.url.path
        if host == "graph.facebook.com":
            return self.graph(request, path.removeprefix("/v25.0/"))
        if host == "lookaside.fbsbx.com":
            return httpx.Response(200, content=OGG, headers={"content-type": "audio/ogg"})
        if host == "gaik-hackathon.example":
            if path.startswith("/api/whatsapp/") and self.app is not None:
                return await self.app.handle_async_request(request)
            return self.api(request, path)
        return httpx.Response(599)

    def graph(self, request: httpx.Request, path: str) -> httpx.Response:
        if path == "app":
            return httpx.Response(200, json={"id": APP_ID, "name": "test-app"})
        if path == f"{APP_ID}/subscriptions":
            return httpx.Response(200, json={"success": True})
        if path == f"{WABA_ID}/subscribed_apps":
            return httpx.Response(200, json={"success": True})
        if path == "MEDIA1":
            return httpx.Response(
                200, json={"url": self.media_url, "mime_type": "audio/ogg; codecs=opus"}
            )
        if path == f"{PHONE_ID}/media":
            return httpx.Response(200, json={"id": "uploaded_1"})
        if path == f"{PHONE_ID}/messages":
            return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})
        return httpx.Response(400, json={"error": {"code": 100, "error_subcode": 33}})

    def api(self, request: httpx.Request, path: str) -> httpx.Response:
        if path in ("/api/intake/text", "/api/intake/audio"):
            return httpx.Response(200, json={"report": REPORT, "reply": REPLY})
        if path == "/api/reports/rep_abc123/files/f_1.docx":
            return httpx.Response(200, content=b"PK-docx")
        if path == "/api/reports/rep_abc123/approve":
            self.approvals += 1
            if self.approvals > 1:
                return httpx.Response(409, json={"error": {"code": "already_sent", "message": "x"}})
            return httpx.Response(200, json={**REPORT, "status": "sent"})
        if path == "/api/reports/rep_abc123/correct":
            return httpx.Response(200, json={**REPORT, "headline": "Loose railing, third floor"})
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "x"}})


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def config(tmp_path):
    return bridge_main.Config(
        access_token=TOKEN,
        app_secret=APP_SECRET,
        bridge_key=BRIDGE_KEY,
        api_base=API,
        allowed=frozenset({SENDER}),
        phone_number_id=PHONE_ID,
        waba_id=WABA_ID,
        state_file=tmp_path / "state" / "seen.json",
    )


@pytest.fixture
async def bridge(world, config):
    async with httpx.AsyncClient(transport=httpx.MockTransport(world.handler)) as client:
        yield bridge_main.Bridge(
            config, bridge_main.Graph(config, client), bridge_main.Api(config, client)
        )


def payload(message: dict[str, Any], sender: str = SENDER) -> bytes:
    body = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": WABA_ID,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": PHONE_ID},
                            "messages": [{"from": sender, "timestamp": "1793530000", **message}],
                        },
                    }
                ],
            }
        ],
    }
    return json.dumps(body, ensure_ascii=False).encode()


def delivery(body: bytes, secret: str = APP_SECRET) -> dict[str, str]:
    return {
        "id": "wh_1",
        "received_at": "2026-11-12T10:00:00.000Z",
        "signature": bridge_main.sign(secret, body),
        "body_b64": base64.b64encode(body).decode(),
    }


def text(message_id: str, body: str) -> dict[str, Any]:
    return {"id": message_id, "type": "text", "text": {"body": body}}


def press(message_id: str, button_id: str) -> dict[str, Any]:
    reply = {"id": button_id, "title": "x"}
    return {
        "id": message_id,
        "type": "interactive",
        "interactive": {"type": "button_reply", "button_reply": reply},
    }


async def feed(bridge, message: dict[str, Any], **kwargs: Any) -> int:
    accepted = bridge.handle_delivery(delivery(payload(message, **kwargs)))
    if bridge.tasks:
        await asyncio.gather(*bridge.tasks)
    return accepted


def check_keys_stay_home(world: World) -> None:
    """The Meta token only to Meta, the bridge key only to the app, no number to the app."""
    for call in world.calls:
        auth = call.headers.get("authorization", "")
        if call.url.host == "gaik-hackathon.example":
            assert auth in ("", f"Bearer {BRIDGE_KEY}")
            assert SENDER.encode() not in call.content
            assert SENDER not in str(call.url)
        else:
            assert BRIDGE_KEY not in auth
            assert call.url.host in ("graph.facebook.com", "lookaside.fbsbx.com")
        assert TOKEN not in str(call.url) and APP_SECRET not in str(call.url)


async def test_text_becomes_a_report_with_summary_document_and_buttons(bridge, world):
    assert await feed(bridge, text("wamid.1", "Railing is loose on the second floor")) == 1

    (intake,) = world.named("POST", "/api/intake/text")
    assert json.loads(intake.content) == {
        "text": "Railing is loose on the second floor",
        "channel": "whatsapp",
        "caller_label": "WhatsApp",
    }
    assert intake.headers["authorization"] == f"Bearer {BRIDGE_KEY}"

    (upload,) = world.named("POST", f"/{PHONE_ID}/media")
    assert b"PK-docx" in upload.content and bridge_main.DOCX_MIME.encode() in upload.content

    typing, summary, document, buttons = world.sent()
    assert typing["status"] == "read" and typing["message_id"] == "wamid.1"
    assert summary["type"] == "text" and summary["text"]["body"] == REPLY
    assert summary["to"] == SENDER
    assert document["document"] == {"id": "uploaded_1", "filename": "Safety observation.docx"}
    replies = [b["reply"] for b in buttons["interactive"]["action"]["buttons"]]
    assert replies == [
        {"id": "approve:rep_abc123", "title": "Approve and send"},
        {"id": "fix:rep_abc123", "title": "Correct"},
    ]
    assert all(len(reply["title"]) <= 20 for reply in replies)
    check_keys_stay_home(world)


async def test_voice_note_is_downloaded_with_two_gets_and_sent_to_intake(bridge, world):
    note = {"id": "MEDIA1", "mime_type": "audio/ogg; codecs=opus", "voice": True}
    assert await feed(bridge, {"id": "wamid.2", "type": "audio", "audio": note}) == 1

    (info,) = world.named("GET", "/MEDIA1")
    assert info.headers["authorization"] == f"Bearer {TOKEN}"
    (download,) = [c for c in world.calls if c.url.host == "lookaside.fbsbx.com"]
    assert download.headers["authorization"] == f"Bearer {TOKEN}"

    (intake,) = world.named("POST", "/api/intake/audio")
    assert OGG in intake.content
    assert b'filename="voice.ogg"' in intake.content and b"audio/ogg" in intake.content
    assert b'name="channel"\r\n\r\nwhatsapp' in intake.content
    assert b'name="caller_label"\r\n\r\nWhatsApp' in intake.content
    assert [m["type"] for m in world.sent()[1:]] == ["text", "document", "interactive"]
    check_keys_stay_home(world)


async def test_token_is_not_sent_to_a_foreign_media_host(bridge, world):
    world.media_url = "https://fbsbx.com.evil.example/file"
    note = {"id": "MEDIA1", "mime_type": "audio/ogg"}
    await feed(bridge, {"id": "wamid.3", "type": "audio", "audio": note})
    assert all(c.url.host != "fbsbx.com.evil.example" for c in world.calls)
    assert not world.named("POST", "/api/intake/audio")
    assert world.sent()[-1]["text"]["body"] == bridge_main.TEXT_FAILED


async def test_approve_sends_no_recipient_and_works_once(bridge, world):
    await feed(bridge, text("wamid.1", "Railing is loose"))
    await feed(bridge, press("wamid.4", "approve:rep_abc123"))

    (approve,) = world.named("POST", "/approve")
    assert json.loads(approve.content) == {}
    assert world.sent()[-1]["text"]["body"] == bridge_main.TEXT_APPROVED

    await feed(bridge, press("wamid.5", "approve:rep_abc123"))  # the old button, tapped again
    assert len(world.named("POST", "/approve")) == 1
    assert world.sent()[-1]["text"]["body"] == bridge_main.TEXT_NOT_OPEN
    check_keys_stay_home(world)


async def test_correct_takes_the_next_message_as_the_correction(bridge, world):
    await feed(bridge, text("wamid.1", "Railing is loose"))
    await feed(bridge, press("wamid.6", "fix:rep_abc123"))
    assert world.sent()[-1]["text"]["body"] == bridge_main.TEXT_ASK_FIX

    await feed(bridge, text("wamid.7", "It was the third floor"))
    (correct,) = world.named("POST", "/correct")
    assert json.loads(correct.content) == {"text": "It was the third floor"}
    assert len(world.named("POST", "/api/intake/text")) == 1  # not a new report
    summary, document, buttons = world.sent()[-3:]
    assert "Loose railing, third floor" in summary["text"]["body"]
    assert document["type"] == "document" and buttons["type"] == "interactive"

    await feed(bridge, text("wamid.8", "A new observation"))  # the correction was used up
    assert len(world.named("POST", "/api/intake/text")) == 2


async def test_button_for_an_unknown_report_is_refused(bridge, world):
    await feed(bridge, press("wamid.9", "approve:rep_other"))
    assert not world.named("POST", "/approve")
    assert world.sent()[-1]["text"]["body"] == bridge_main.TEXT_NOT_OPEN


async def test_wrong_signature_is_dropped_before_parsing(bridge, world):
    body = payload(text("wamid.10", "hello"))
    assert bridge.handle_delivery(delivery(body, secret="another-secret")) == 0
    tampered = delivery(body)
    tampered["body_b64"] = base64.b64encode(body + b" ").decode()  # one byte more
    assert bridge.handle_delivery(tampered) == 0
    assert bridge.handle_delivery({"signature": "sha256=00", "body_b64": "%%%"}) == 0
    assert world.calls == [] and not bridge.tasks


async def test_unknown_sender_gets_nothing(bridge, world):
    assert await feed(bridge, text("wamid.11", "hello"), sender=STRANGER) == 0
    assert world.calls == []


async def test_duplicate_message_id_is_handled_once_also_after_a_restart(bridge, world, config):
    assert await feed(bridge, text("wamid.12", "Railing is loose")) == 1
    assert await feed(bridge, text("wamid.12", "Railing is loose")) == 0
    assert len(world.named("POST", "/api/intake/text")) == 1

    async with httpx.AsyncClient(transport=httpx.MockTransport(world.handler)) as client:
        restarted = bridge_main.Bridge(
            config, bridge_main.Graph(config, client), bridge_main.Api(config, client)
        )
        assert await feed(restarted, text("wamid.12", "Railing is loose")) == 0
    assert len(world.named("POST", "/api/intake/text")) == 1
    assert json.loads(config.state_file.read_text()) == ["wamid.12"]
    assert SENDER not in config.state_file.read_text()


async def test_nothing_is_sent_outside_the_24_hour_window(bridge, world):
    now = [1_000_000.0]
    bridge.clock = lambda: now[0]
    bridge.last_inbound[SENDER] = now[0]
    assert bridge.window_open(SENDER)
    now[0] += bridge_main.WINDOW_S + 60
    assert not bridge.window_open(SENDER)
    assert await bridge._say(SENDER, "late") is False
    assert not bridge.window_open(STRANGER)  # never heard from: closed
    assert world.calls == []


async def test_other_numbers_webhooks_and_statuses_are_ignored(bridge, world):
    body = json.loads(payload(text("wamid.13", "hello")))
    body["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "999"
    raw = json.dumps(body).encode()
    assert bridge.handle_delivery(delivery(raw)) == 0
    statuses = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": PHONE_ID},
                            "statuses": [
                                {
                                    "status": "failed",
                                    "recipient_id": SENDER,
                                    "errors": [{"code": 131047}],
                                }
                            ],
                        },
                    }
                ]
            }
        ],
    }
    bridge.last_inbound[SENDER] = bridge.clock()
    assert bridge.handle_delivery(delivery(json.dumps(statuses).encode())) == 0
    assert not bridge.window_open(SENDER)  # Meta said the window is closed
    assert world.calls == []


async def test_run_attaches_polls_and_detaches_against_the_real_endpoints(bridge, world, app):
    """Session, handshake probe, subscribe, a signed delivery through the app's own webhook
    and long poll, then detach and session end."""
    from app.whatsapp.queue import get_hub

    world.app = httpx.ASGITransport(app=app)
    await bridge.attach()
    assert get_hub().active() and bridge.attached

    (probe,) = world.named("GET", "/api/whatsapp/webhook")
    assert "authorization" not in probe.headers
    subscribe = world.named("POST", f"/{APP_ID}/subscriptions")[0]
    assert subscribe.headers["authorization"] == f"Bearer {APP_ID}|{APP_SECRET}"
    form = dict(httpx.QueryParams(subscribe.content.decode()))
    assert form["object"] == "whatsapp_business_account" and form["fields"] == "messages"
    assert form["callback_url"] == f"{API}/api/whatsapp/webhook"
    assert world.named("POST", f"/{WABA_ID}/subscribed_apps")

    body = payload(text("wamid.20", "Railing is loose"))
    async with httpx.AsyncClient(transport=world.app, base_url=API) as meta:
        signed = {"X-Hub-Signature-256": bridge_main.sign(APP_SECRET, body)}
        assert (await meta.post("/api/whatsapp/webhook", content=body, headers=signed)).is_success
        forged = {"X-Hub-Signature-256": bridge_main.sign("forged", body)}
        assert (await meta.post("/api/whatsapp/webhook", content=body, headers=forged)).is_success

    assert await bridge.poll_once(wait=1) == 1  # the forged copy fails the HMAC check
    await asyncio.gather(*bridge.tasks)
    assert len(world.named("POST", "/api/intake/text")) == 1

    await bridge.close()
    (detach,) = world.named("DELETE", f"/{APP_ID}/subscriptions")
    assert detach.url.params["object"] == "whatsapp_business_account"
    assert not get_hub().active() and not bridge.attached
    check_keys_stay_home(world)


def test_load_config_refuses_empty_and_undecrypted_values(tmp_path):
    env = {
        "WHATSAPP_ACCESS_TOKEN": TOKEN,
        "WHATSAPP_APP_SECRET": APP_SECRET,
        "LUMI_ALLOWED": "+1 (000) 000-0001, 100000000002",
        "HACK_BRIDGE_KEY": BRIDGE_KEY,
        "PUBLIC_BASE_URL": API + "/",
    }
    config = bridge_main.load_config(env=env)
    assert config.phone_number_id == "1491262427399689"
    assert config.waba_id == "4624332917843413"
    assert config.graph_version == "v25.0" and config.api_base == API
    assert config.allowed == {"10000000001", "100000000002"}
    assert TOKEN not in repr(config) and APP_SECRET not in repr(config)

    for name in ("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_APP_SECRET", "LUMI_ALLOWED", "HACK_BRIDGE_KEY"):
        for bad in ("", "  encrypted:BF9x"):
            with pytest.raises(bridge_main.ConfigError) as caught:
                bridge_main.load_config(env={**env, name: bad})
            assert name in str(caught.value) and "BF9x" not in str(caught.value)
    with pytest.raises(bridge_main.ConfigError):
        bridge_main.load_config(env={**env, "PUBLIC_BASE_URL": "http://example.com"})

    # ids fall back to the env library names when config.json has none
    fallback = bridge_main.load_config(
        env={**env, "WHATSAPP_PHONE_NUMBER_ID": "1", "WHATSAPP_BUSINESS_ACCOUNT_ID": "2"},
        config_file=tmp_path / "missing.json",
    )
    assert (fallback.phone_number_id, fallback.waba_id) == ("1", "2")
