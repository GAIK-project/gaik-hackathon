"""WhatsApp bridge: runs on the owner's machine and is the only holder of the Meta keys.

    run.ps1 -PublicBaseUrl https://<app>      start (loads the keys, then runs this file)
    run.ps1 -Status                           read-only check of the number and the account
    run.ps1 -Stop [-PublicBaseUrl ...]        detach the webhook after a cut-off run

The app (docs/ARCHITECTURE.md sections 9 and 15.11) only stores what Meta sends. This process:

1. starts a bridge session at the app (`POST /api/whatsapp/session`) with a random verify token,
2. probes its own handshake through the public URL, then points the Meta app's webhook
   (field `messages`) at `<public URL>/api/whatsapp/webhook`,
3. long-polls `GET /api/whatsapp/pending`, checks every delivery's HMAC over the exact bytes
   with the app secret, drops senders that are not allowed and message ids already seen,
4. sends text and voice notes to the intake endpoints, answers with the summary, the Word file
   and two buttons, and runs the approve and correct flows,
5. on exit removes the webhook subscription and ends the session.

The phone number never reaches the app: intake calls carry `caller_label="WhatsApp"` only.
The access token goes only to the Graph host and Meta's media hosts, the bridge key only to
the app. Nothing secret is logged; a sender is never logged. Voice calls are answered only
with `--calls` (or `BRIDGE_CALLS=1`): see `calls.py` and CALLS.md.

Environment (set by run.ps1, never stored): `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET`,
`LUMI_ALLOWED`, `HACK_BRIDGE_KEY`, `PUBLIC_BASE_URL`. Ids come from `config.json`.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

logger = logging.getLogger("bridge")

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "config.json"
STATE_FILE = HERE / ".state" / "seen.json"

GRAPH_URL = "https://graph.facebook.com"
MEDIA_HOSTS = ("fbcdn.net", "fbsbx.com")
WEBHOOK_OBJECT = "whatsapp_business_account"
WINDOW_S = 24 * 60 * 60
SEEN_MAX = 2000
TEXT_MAX = 4096
BODY_MAX = 1024
MEDIA_MAX_BYTES = 25 * 1024 * 1024
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
LOCAL_HOSTS = ("localhost", "127.0.0.1")
AUDIO_NAMES = (".ogg", ".opus", ".m4a", ".mp3", ".wav")
AUDIO_FILES = {
    "audio/ogg": "voice.ogg",
    "audio/opus": "voice.ogg",
    "audio/mpeg": "voice.mp3",
    "audio/mp4": "voice.m4a",
    "audio/aac": "voice.m4a",
    "audio/wav": "voice.wav",
    "audio/webm": "voice.webm",
}

# What the sender reads in WhatsApp.
BUTTON_APPROVE = "Approve and send"
BUTTON_FIX = "Correct"
TEXT_DECIDE = "Nothing is sent before you decide."
TEXT_APPROVED = "Approved. The report has been sent."
TEXT_ALREADY_SENT = "This report has already been sent."
TEXT_NOT_OPEN = "This proposal is no longer open."
TEXT_ASK_FIX = "Tell me what to change, as text or as a voice note."
TEXT_FAILED = "Processing failed. Please try again."
TEXT_APPROVE_FAILED = "Sending failed. Please try again."
TEXT_UNSUPPORTED = "I can read text and voice notes."
TEXT_UPDATED = "Updated."


class ConfigError(Exception):
    """A setting is missing or unusable. The message names settings, never values."""


class GraphError(Exception):
    """Graph answered with an error. Holds the status and Meta's codes, never the request."""

    def __init__(self, what: str, status: int, code: object = None, subcode: object = None):
        super().__init__(f"Graph {what}: HTTP {status}, code {code}, subcode {subcode}")
        self.status = status
        self.code = code
        self.subcode = subcode


class ApiError(Exception):
    """The app answered with an error."""

    def __init__(self, what: str, status: int, code: str | None = None):
        super().__init__(f"API {what}: HTTP {status}, code {code}")
        self.status = status
        self.code = code


# configuration ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    access_token: str = field(repr=False)
    app_secret: str = field(repr=False, default="")
    bridge_key: str = field(repr=False, default="")
    api_base: str = ""
    allowed: frozenset[str] = field(repr=False, default=frozenset())
    phone_number_id: str = ""
    waba_id: str = ""
    graph_version: str = "v25.0"
    graph_url: str = GRAPH_URL
    state_file: Path = STATE_FILE


def _secret(env: dict[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is not set")
    if value.startswith("encrypted:"):
        raise ConfigError(f"{name} was not decrypted")
    return value


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def check_api_base(url: str) -> str:
    parts = urlsplit(url.strip())
    local = parts.scheme == "http" and (parts.hostname or "") in LOCAL_HOSTS
    if not parts.hostname or not (parts.scheme == "https" or local):
        raise ConfigError("PUBLIC_BASE_URL must be an https origin")
    return f"{parts.scheme}://{parts.netloc}"


def load_config(
    need: tuple[str, ...] = ("app_secret", "allowed", "api"),
    env: dict[str, str] | None = None,
    config_file: Path = CONFIG_FILE,
) -> Config:
    """Build the configuration from `config.json` and the process environment.

    `need` lists what the command uses beyond the access token: `app_secret`, `allowed`
    (the allowed senders) and `api` (the app's address and the bridge key).
    """
    env = dict(os.environ) if env is None else env
    try:
        stored = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    version = str(stored.get("graph_version") or "v25.0")
    phone_id = str(stored.get("phone_number_id") or env.get("WHATSAPP_PHONE_NUMBER_ID", "")).strip()
    waba_id = str(
        stored.get("whatsapp_business_account_id") or env.get("WHATSAPP_BUSINESS_ACCOUNT_ID", "")
    ).strip()
    if not re.fullmatch(r"v\d+\.\d+", version):
        raise ConfigError("graph_version in config.json must look like v25.0")
    for name, value in (("phone_number_id", phone_id), ("whatsapp_business_account_id", waba_id)):
        if not value.isdigit():
            raise ConfigError(f"{name} must be digits (config.json)")

    allowed: frozenset[str] = frozenset()
    if "allowed" in need:
        numbers = (_digits(part) for part in _secret(env, "LUMI_ALLOWED").split(","))
        allowed = frozenset(number for number in numbers if number)
        if not allowed:
            raise ConfigError("LUMI_ALLOWED holds no number")
    return Config(
        access_token=_secret(env, "WHATSAPP_ACCESS_TOKEN"),
        app_secret=_secret(env, "WHATSAPP_APP_SECRET") if "app_secret" in need else "",
        bridge_key=_secret(env, "HACK_BRIDGE_KEY") if "api" in need else "",
        api_base=check_api_base(env.get("PUBLIC_BASE_URL", "")) if "api" in need else "",
        allowed=allowed,
        phone_number_id=phone_id,
        waba_id=waba_id,
        graph_version=version,
    )


# signature -------------------------------------------------------------------------------


def sign(app_secret: str, body: bytes) -> str:
    digest = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def signature_ok(app_secret: str, body: bytes, header: str | None) -> bool:
    """`X-Hub-Signature-256` is the HMAC-SHA256 of the exact bytes Meta sent."""
    return hmac.compare_digest(sign(app_secret, body).encode(), (header or "").encode())


# Graph -----------------------------------------------------------------------------------


def _media_host_ok(url: str, graph_url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        return False
    if host == (urlsplit(graph_url).hostname or "").lower():
        return True
    return any(host == name or host.endswith("." + name) for name in MEDIA_HOSTS)


class Graph:
    """The Graph API calls the bridge makes. The token travels in the Authorization header
    and only to the Graph host or Meta's media hosts."""

    def __init__(self, config: Config, client: httpx.AsyncClient) -> None:
        self.config = config
        self.client = client
        self.base = f"{config.graph_url}/{config.graph_version}"
        self._app_id: str | None = None

    async def _call(
        self, what: str, method: str, path: str, *, token: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token or self.config.access_token}"}
        try:
            response = await self.client.request(
                method, f"{self.base}/{path}", headers=headers, **kwargs
            )
        except httpx.HTTPError as error:
            raise GraphError(f"{what} ({type(error).__name__})", 0) from None
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400 or not isinstance(data, dict):
            problem = data.get("error", {}) if isinstance(data, dict) else {}
            raise GraphError(
                what, response.status_code, problem.get("code"), problem.get("error_subcode")
            )
        return data

    async def app_id(self) -> str:
        if self._app_id is None:
            self._app_id = str((await self._call("app", "GET", "app"))["id"])
        return self._app_id

    async def _app_token(self) -> str:
        return f"{await self.app_id()}|{self.config.app_secret}"

    # webhook subscription

    async def subscriptions(self) -> list[dict[str, Any]]:
        data = await self._call(
            "subscriptions",
            "GET",
            f"{await self.app_id()}/subscriptions",
            token=await self._app_token(),
        )
        return list(data.get("data") or [])

    async def subscribe(
        self, callback_url: str, verify_token: str, fields: str = "messages"
    ) -> None:
        await self._call(
            "subscribe app",
            "POST",
            f"{await self.app_id()}/subscriptions",
            token=await self._app_token(),
            data={
                "object": WEBHOOK_OBJECT,
                "callback_url": callback_url,
                "verify_token": verify_token,
                "fields": fields,
            },
        )
        await self._call("subscribe account", "POST", f"{self.config.waba_id}/subscribed_apps")

    async def detach(self) -> None:
        # Always with `object`: without it Meta removes every subscription of the app.
        await self._call(
            "detach",
            "DELETE",
            f"{await self.app_id()}/subscriptions",
            token=await self._app_token(),
            params={"object": WEBHOOK_OBJECT},
        )

    # read-only status

    async def phone_number(self) -> dict[str, Any]:
        return await self._call(
            "phone number",
            "GET",
            self.config.phone_number_id,
            params={"fields": "id,quality_rating,code_verification_status,health_status"},
        )

    async def subscribed_apps(self) -> list[dict[str, Any]]:
        data = await self._call("subscribed apps", "GET", f"{self.config.waba_id}/subscribed_apps")
        return list(data.get("data") or [])

    # media

    async def download(self, media_id: str) -> tuple[bytes, str]:
        """A media file: one GET for its address, one for the bytes (both with the token)."""
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", media_id):
            raise GraphError("media id", 0)
        info = await self._call("media info", "GET", media_id)
        url = str(info.get("url") or "")
        mime = str(info.get("mime_type") or "")
        headers = {"Authorization": f"Bearer {self.config.access_token}"}
        for _hop in range(5):
            if not _media_host_ok(url, self.config.graph_url):
                raise GraphError("media host refused", 0)
            try:
                response = await self.client.get(url, headers=headers, follow_redirects=False)
            except httpx.HTTPError as error:
                raise GraphError(f"media download ({type(error).__name__})", 0) from None
            if response.is_redirect:
                url = urljoin(url, response.headers.get("location", ""))
                continue
            if response.status_code != 200:
                raise GraphError("media download", response.status_code)
            if len(response.content) > MEDIA_MAX_BYTES:
                raise GraphError("media too large", 0)
            return response.content, mime or response.headers.get("content-type", "")
        raise GraphError("media redirects", 0)

    async def upload(self, data: bytes, filename: str, mime: str) -> str:
        answer = await self._call(
            "media upload",
            "POST",
            f"{self.config.phone_number_id}/media",
            data={"messaging_product": "whatsapp"},
            files={"file": (filename, data, mime)},
        )
        return str(answer["id"])

    # messages

    async def _send(self, what: str, payload: dict[str, Any]) -> None:
        body = {"messaging_product": "whatsapp", **payload}
        await self._call(what, "POST", f"{self.config.phone_number_id}/messages", json=body)

    async def send_text(self, to: str, text: str) -> None:
        text = text.strip()[:TEXT_MAX] or "."
        await self._send(
            "send text",
            {
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": text},
            },
        )

    async def send_buttons(self, to: str, body: str, buttons: list[tuple[str, str]]) -> None:
        replies = [
            {"type": "reply", "reply": {"id": key, "title": title[:20]}}
            for key, title in buttons[:3]
        ]
        await self._send(
            "send buttons",
            {
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": body[:BODY_MAX]},
                    "action": {"buttons": replies},
                },
            },
        )

    async def send_document(self, to: str, media_id: str, filename: str) -> None:
        await self._send(
            "send document",
            {
                "recipient_type": "individual",
                "to": to,
                "type": "document",
                "document": {"id": media_id, "filename": filename},
            },
        )

    async def typing(self, message_id: str) -> None:
        """Marks the message read and shows the typing indicator. A gesture only."""
        await self._send(
            "typing",
            {
                "status": "read",
                "message_id": message_id,
                "typing_indicator": {"type": "text"},
            },
        )


# the app ---------------------------------------------------------------------------------


class Api:
    """The app's endpoints, called with the bridge key. The key goes only to `api_base`."""

    def __init__(self, config: Config, client: httpx.AsyncClient) -> None:
        self.base = config.api_base
        self.client = client
        self.headers = {"Authorization": f"Bearer {config.bridge_key}"}

    async def _call(
        self,
        what: str,
        method: str,
        path: str,
        *,
        limit_s: float = 30.0,
        **kwargs: Any,
    ) -> httpx.Response:
        if not path.startswith("/") or path.startswith("//"):
            raise ApiError(f"{what} (path refused)", 0)
        try:
            response = await self.client.request(
                method, f"{self.base}{path}", headers=self.headers, timeout=limit_s, **kwargs
            )
        except httpx.HTTPError as error:
            raise ApiError(f"{what} ({type(error).__name__})", 0) from None
        if response.status_code >= 400:
            code = None
            with contextlib.suppress(ValueError, AttributeError):
                code = response.json().get("error", {}).get("code")
            raise ApiError(what, response.status_code, code)
        return response

    async def start_session(self, verify_token: str) -> str:
        response = await self._call(
            "start session", "POST", "/api/whatsapp/session", json={"verify_token": verify_token}
        )
        return str(response.json()["callback_url"])

    async def end_session(self) -> None:
        await self._call("end session", "DELETE", "/api/whatsapp/session")

    async def pending(self, wait: float = 25.0) -> list[dict[str, Any]]:
        response = await self._call(
            "pending", "GET", "/api/whatsapp/pending", params={"wait": wait}, limit_s=wait + 20
        )
        return list(response.json().get("items") or [])

    async def intake_text(self, text: str) -> dict[str, Any]:
        response = await self._call(
            "intake text",
            "POST",
            "/api/intake/text",
            limit_s=300,
            json={"text": text, "channel": "whatsapp", "caller_label": "WhatsApp"},
        )
        return response.json()

    async def intake_audio(self, data: bytes, filename: str, mime: str) -> dict[str, Any]:
        response = await self._call(
            "intake audio",
            "POST",
            "/api/intake/audio",
            limit_s=300,
            data={"channel": "whatsapp", "caller_label": "WhatsApp"},
            files={"file": (filename, data, mime)},
        )
        return response.json()

    async def approve(self, report_id: str) -> dict[str, Any]:
        # No `to`: the server's default recipient. An address typed in WhatsApp never passes.
        response = await self._call(
            "approve", "POST", f"/api/reports/{report_id}/approve", limit_s=60, json={}
        )
        return response.json()

    async def correct_text(self, report_id: str, text: str) -> dict[str, Any]:
        response = await self._call(
            "correct", "POST", f"/api/reports/{report_id}/correct", limit_s=300, json={"text": text}
        )
        return response.json()

    async def correct_audio(
        self, report_id: str, data: bytes, filename: str, mime: str
    ) -> dict[str, Any]:
        response = await self._call(
            "correct",
            "POST",
            f"/api/reports/{report_id}/correct",
            limit_s=300,
            files={"file": (filename, data, mime)},
        )
        return response.json()

    async def file(self, url: str) -> bytes:
        return (await self._call("report file", "GET", url, limit_s=60)).content


# de-duplication --------------------------------------------------------------------------


class Seen:
    """Message ids already handled, kept in a small local file across restarts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.ids: list[str] = []
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.ids = [item for item in stored if isinstance(item, str)][-SEEN_MAX:]
        except (OSError, ValueError):
            pass
        self._set = set(self.ids)

    def add(self, message_id: str) -> bool:
        """Record an id. False when it was already there."""
        if message_id in self._set:
            return False
        self.ids.append(message_id)
        self._set.add(message_id)
        if len(self.ids) > SEEN_MAX:
            self.ids = self.ids[-SEEN_MAX:]
            self._set = set(self.ids)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.ids), encoding="utf-8")
            temp.replace(self.path)
        except OSError:
            logger.warning("could not save the list of handled message ids")
        return True


# the flow --------------------------------------------------------------------------------


def _report_id_ok(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9_\-]{1,80}", value) is not None


def _audio_file(mime: str) -> tuple[str, str]:
    base = mime.split(";", 1)[0].strip().lower()
    return AUDIO_FILES.get(base, "voice.ogg"), base if base in AUDIO_FILES else "audio/ogg"


class Bridge:
    def __init__(
        self,
        config: Config,
        graph: Graph,
        api: Api,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.graph = graph
        self.api = api
        self.clock = clock
        self.seen = Seen(config.state_file)
        self.last_inbound: dict[str, float] = {}  # sender -> time of their last message
        self.open: dict[str, str] = {}  # report id -> sender; one decision per report
        self.correcting: dict[str, str] = {}  # sender -> report id waiting for a correction
        self.tasks: set[asyncio.Task[None]] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self.attached = False
        self.calls = False  # --calls: also answer voice calls (calls.py)

    # inbound ----------------------------------------------------------------------------

    def handle_delivery(self, item: dict[str, Any]) -> int:
        """Check one queued delivery and start work on its messages. Returns how many
        messages were accepted. Never waits: the poll loop goes straight on."""
        try:
            body = base64.b64decode(item.get("body_b64") or "", validate=True)
        except ValueError:
            logger.warning("delivery dropped: body is not base64")
            return 0
        if not signature_ok(self.config.app_secret, body, item.get("signature")):
            logger.warning("delivery dropped: wrong signature")
            return 0
        try:
            payload = json.loads(body)
        except ValueError:
            logger.warning("delivery dropped: not JSON")
            return 0
        accepted = 0
        for message in self._messages(payload):
            sender = str(message.get("from") or "")
            message_id = str(message.get("id") or "")
            if sender not in self.config.allowed:
                logger.info("message ignored: sender is not allowed")
                continue
            if not message_id or not self.seen.add(message_id):
                logger.info("message ignored: already handled")
                continue
            self.last_inbound[sender] = self.clock()  # a message or a press opens the window
            task = asyncio.create_task(self._process(sender, message))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            accepted += 1
        if self.calls:
            self._route_calls(payload)
        return accepted

    def _route_calls(self, payload: Any) -> None:
        """Hand every change of field `calls` for our number to calls.py (it checks the caller)."""
        import calls  # aiortc loads only when a call comes

        if not isinstance(payload, dict) or payload.get("object") != WEBHOOK_OBJECT:
            return
        for entry in payload.get("entry") or []:
            for change in (entry or {}).get("changes") or []:
                value = (change or {}).get("value")
                if (change or {}).get("field") != calls.SUBSCRIBE_FIELD:
                    continue
                if not isinstance(value, dict):
                    continue
                number_id = str((value.get("metadata") or {}).get("phone_number_id") or "")
                if number_id != self.config.phone_number_id:
                    continue
                task = asyncio.create_task(
                    calls.handle_call_event(value, self.graph.client, self.config)
                )
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)

    def _messages(self, payload: Any) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        if not isinstance(payload, dict) or payload.get("object") != WEBHOOK_OBJECT:
            return found
        for entry in payload.get("entry") or []:
            for change in (entry or {}).get("changes") or []:
                value = (change or {}).get("value") or {}
                if (change or {}).get("field") != "messages" or not isinstance(value, dict):
                    continue
                number_id = str((value.get("metadata") or {}).get("phone_number_id") or "")
                if number_id != self.config.phone_number_id:
                    continue
                for status in value.get("statuses") or []:
                    self._note_status(status)
                found.extend(m for m in value.get("messages") or [] if isinstance(m, dict))
        return found

    def _note_status(self, status: Any) -> None:
        if not isinstance(status, dict) or status.get("status") != "failed":
            return
        codes = [error.get("code") for error in status.get("errors") or []]
        logger.warning("Meta reported a failed delivery, codes %s", codes)
        if 131047 in codes:  # more than 24 hours since the person's last message
            self.last_inbound.pop(str(status.get("recipient_id") or ""), None)

    # processing -------------------------------------------------------------------------

    async def _process(self, sender: str, message: dict[str, Any]) -> None:
        lock = self._locks.setdefault(sender, asyncio.Lock())
        async with lock:  # one sender's messages are handled in the order they came
            try:
                await self._dispatch(sender, message)
            except (GraphError, ApiError) as error:
                logger.error("message failed: %s", error)
            except Exception as error:  # noqa: BLE001 - one message must not stop the bridge
                logger.error("message failed: %s", type(error).__name__)

    async def _dispatch(self, sender: str, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "interactive":
            reply = (message.get("interactive") or {}).get("button_reply") or {}
            await self._button(sender, str(reply.get("id") or ""))
            return
        with contextlib.suppress(GraphError):
            await self.graph.typing(str(message.get("id")))
        if kind == "text":
            text = str((message.get("text") or {}).get("body") or "").strip()
            if text:
                await self._content(sender, text=text)
            return
        media = None
        if kind in ("audio", "voice"):
            media = message.get(kind) or {}
        elif kind == "document":
            document = message.get("document") or {}
            name = str(document.get("filename") or "").lower()
            if name.endswith(AUDIO_NAMES):  # WhatsApp Web sends an audio file as a document
                media = document
        if not media or not media.get("id"):
            await self._say(sender, TEXT_UNSUPPORTED)
            return
        try:
            data, mime = await self.graph.download(str(media["id"]))
        except GraphError as error:
            logger.error("voice note not downloaded: %s", error)
            await self._say(sender, TEXT_FAILED)
            return
        filename, mime = _audio_file(mime or str(media.get("mime_type") or ""))
        await self._content(sender, audio=(data, filename, mime))

    async def _content(
        self, sender: str, *, text: str | None = None, audio: tuple[bytes, str, str] | None = None
    ) -> None:
        """A text or a voice note: a correction when one was asked for, else a new report."""
        report_id = self.correcting.pop(sender, None)
        try:
            if report_id is not None:
                if text is not None:
                    report = await self.api.correct_text(report_id, text)
                else:
                    report = await self.api.correct_audio(report_id, *audio)  # type: ignore[misc]
                summary = _corrected_summary(report)
            else:
                if text is not None:
                    answer = await self.api.intake_text(text)
                else:
                    answer = await self.api.intake_audio(*audio)  # type: ignore[misc]
                report = answer.get("report") or {}
                summary = str(answer.get("reply") or "").strip() or _corrected_summary(report)
        except ApiError as error:
            logger.error("report not made: %s", error)
            if report_id is not None:
                self.correcting[sender] = report_id  # the next message may still correct it
            await self._say(sender, TEXT_FAILED)
            return
        await self._answer(sender, report, summary)

    async def _answer(self, sender: str, report: dict[str, Any], summary: str) -> None:
        """The summary, the Word file as a document, and the two buttons."""
        if not await self._say(sender, summary):
            return
        report_id = str(report.get("id") or "")
        if not _report_id_ok(report_id):
            return
        docx = next((f for f in report.get("files") or [] if f.get("kind") == "docx"), None)
        if docx and str(docx.get("url") or "").startswith("/api/"):
            try:
                data = await self.api.file(str(docx["url"]))
                media_id = await self.graph.upload(data, "report.docx", DOCX_MIME)
                await self.graph.send_document(sender, media_id, _document_name(report))
            except (ApiError, GraphError) as error:
                logger.error("Word file not delivered: %s", error)
        if report.get("status") != "needs_approval":
            return
        self.open[report_id] = sender
        await self.graph.send_buttons(
            sender,
            TEXT_DECIDE,
            [(f"approve:{report_id}", BUTTON_APPROVE), (f"fix:{report_id}", BUTTON_FIX)],
        )

    async def _button(self, sender: str, button_id: str) -> None:
        action, _, report_id = button_id.partition(":")
        if action not in ("approve", "fix") or self.open.get(report_id) != sender:
            await self._say(sender, TEXT_NOT_OPEN)  # old buttons stay tappable for ever
            return
        if action == "fix":
            self.correcting[sender] = report_id
            await self._say(sender, TEXT_ASK_FIX)
            return
        del self.open[report_id]  # one decision per report
        self.correcting.pop(sender, None)
        try:
            await self.api.approve(report_id)
        except ApiError as error:
            logger.error("approve failed: %s", error)
            if error.status == 409:
                await self._say(sender, TEXT_ALREADY_SENT)
            else:
                self.open[report_id] = sender
                await self._say(sender, TEXT_APPROVE_FAILED)
            return
        await self._say(sender, TEXT_APPROVED)

    # outbound ---------------------------------------------------------------------------

    def window_open(self, to: str) -> bool:
        """Meta takes a free-form message only within 24 hours of the person's last one."""
        return self.clock() - self.last_inbound.get(to, float("-inf")) < WINDOW_S

    async def _say(self, to: str, text: str) -> bool:
        if not self.window_open(to):
            logger.warning("not sent: the 24-hour window is closed")
            return False
        await self.graph.send_text(to, text)
        return True

    # run --------------------------------------------------------------------------------

    async def probe(self, callback_url: str, verify_token: str, tries: int = 20) -> None:
        """Ask our own handshake through the public address, the way Meta will."""
        if check_api_base(callback_url) != self.config.api_base:
            raise ConfigError("the app's callback address is not under PUBLIC_BASE_URL")
        challenge = secrets.token_hex(8)
        params = {
            "hub.mode": "subscribe",
            "hub.verify_token": verify_token,
            "hub.challenge": challenge,
        }
        for attempt in range(tries):
            try:
                response = await self.api.client.get(callback_url, params=params, timeout=15)
                if response.status_code == 200 and response.text == challenge:
                    return
            except httpx.HTTPError:
                pass
            if attempt + 1 < tries:
                await asyncio.sleep(2)
        raise ConfigError("the public webhook address did not answer the handshake")

    async def attach(self) -> None:
        verify_token = secrets.token_hex(24)
        callback_url = await self.api.start_session(verify_token)
        logger.info("session started; probing the handshake")
        await self.probe(callback_url, verify_token)
        fields = "messages"
        if self.calls:
            import calls

            fields = f"messages,{calls.SUBSCRIBE_FIELD}"
        await self.graph.subscribe(callback_url, verify_token, fields)
        self.attached = True
        logger.info("webhook attached (fields %s)", fields)
        if self.calls and not await calls.enable_calling(self.graph.client, self.config):
            logger.warning("calling could not be enabled on the number; calls may not arrive")

    async def poll_once(self, wait: float = 25.0) -> int:
        accepted = 0
        for item in await self.api.pending(wait):
            accepted += self.handle_delivery(item)
        return accepted

    async def run(self, minutes: float = 0) -> None:
        deadline = time.monotonic() + minutes * 60 if minutes > 0 else None
        try:
            await self.attach()
            logger.info("listening; Ctrl+C stops and detaches")
            while deadline is None or time.monotonic() < deadline:
                try:
                    await self.poll_once()
                except ApiError as error:
                    if error.code == "no_session":
                        # The app restarted. Meta's subscription still points at it.
                        logger.warning("the app lost the session; starting a new one")
                        with contextlib.suppress(ApiError):
                            await self.api.start_session(secrets.token_hex(24))
                    else:
                        logger.warning("poll failed: %s", error)
                    await asyncio.sleep(3)
        finally:
            await self.close()

    async def close(self) -> None:
        """Detach the webhook, end the session, let running work finish briefly."""
        if self.calls:
            import calls

            with contextlib.suppress(Exception):
                await calls.close_all()  # hang up a call in progress
        try:
            await self.graph.detach()
            logger.info("webhook detached")
        except GraphError as error:
            logger.error("webhook NOT detached (%s): run run.ps1 -Stop", error)
        self.attached = False
        try:
            await self.api.end_session()
            logger.info("session ended")
        except ApiError as error:
            logger.error("session not ended: %s", error)
        if self.tasks:
            _done, running = await asyncio.wait(self.tasks, timeout=20)
            for task in running:
                task.cancel()


def _corrected_summary(report: dict[str, Any]) -> str:
    headline = str(report.get("headline") or report.get("type_title") or "").strip()
    counts = f"{report.get('filled', 0)}/{report.get('total', 0)} required fields filled."
    return " ".join(part for part in (TEXT_UPDATED, headline, counts) if part)


def _document_name(report: dict[str, Any]) -> str:
    title = re.sub(r"[^\w \-]", "", str(report.get("type_title") or "")).strip()[:60]
    return f"{title or 'Report'}.docx"


# commands --------------------------------------------------------------------------------


async def command_status() -> int:
    """Read-only: can the token read the number and the account, and who is subscribed."""
    config = load_config(need=())
    failed = 0
    async with httpx.AsyncClient(timeout=30) as client:
        graph = Graph(config, client)
        try:
            number = await graph.phone_number()
            health = number.get("health_status") or {}
            print(
                "phone number: HTTP 200,"
                f" quality {number.get('quality_rating')},"
                f" code verification {number.get('code_verification_status')},"
                f" can send {health.get('can_send_message')}"
            )
        except GraphError as error:
            failed += 1
            print(f"phone number: HTTP {error.status}, code {error.code}, subcode {error.subcode}")
        try:
            apps = await graph.subscribed_apps()
            names = [str((a.get("whatsapp_business_api_data") or {}).get("name")) for a in apps]
            print(f"subscribed apps: HTTP 200, {len(apps)} app(s) {names}")
        except GraphError as error:
            failed += 1
            print(
                f"subscribed apps: HTTP {error.status}, code {error.code}, subcode {error.subcode}"
            )
    return 1 if failed else 0


async def command_stop() -> int:
    """Detach the webhook after a cut-off run; end the session when the app is known."""
    with_api = bool(os.environ.get("PUBLIC_BASE_URL", "").strip())
    config = load_config(need=("app_secret", "api") if with_api else ("app_secret",))
    code = 0
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            await Graph(config, client).detach()
            print("webhook detached")
        except GraphError as error:
            code = 1
            print(f"webhook not detached: {error}")
        if with_api:
            try:
                await Api(config, client).end_session()
                print("session ended")
            except ApiError as error:
                code = 1
                print(f"session not ended: {error}")
    return code


async def command_run(minutes: float, calls: bool = False) -> int:
    config = load_config()
    async with httpx.AsyncClient(timeout=30) as client:
        bridge = Bridge(config, Graph(config, client), Api(config, client))
        bridge.calls = calls
        logger.info("%d allowed sender(s); app at %s", len(config.allowed), config.api_base)
        await bridge.run(minutes)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WhatsApp bridge for the GAIK mini-hackathon app")
    parser.add_argument("--status", action="store_true", help="read-only check, sends nothing")
    parser.add_argument("--stop", action="store_true", help="detach the webhook and exit")
    parser.add_argument(
        "--minutes", type=float, default=180, help="stop after this long (0: never)"
    )
    parser.add_argument(
        "--calls",
        action="store_true",
        default=os.environ.get("BRIDGE_CALLS") == "1",
        help="also answer WhatsApp voice calls (enables calling on the number; see CALLS.md)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx logs every request line, and the handshake probe carries the verify token there.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        if args.status:
            return asyncio.run(command_status())
        if args.stop:
            return asyncio.run(command_stop())
        return asyncio.run(command_run(args.minutes, args.calls))
    except ConfigError as error:
        print(f"configuration: {error}", file=sys.stderr)
        return 2
    except (GraphError, ApiError) as error:
        print(f"stopped: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
