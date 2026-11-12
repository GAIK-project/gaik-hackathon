"""WhatsApp voice calls: the owner calls the demo number and talks to the app's call agent.

Meta's Calling API brings a call as a `calls` webhook with an SDP offer; the media is WebRTC
with Opus. For every call this module

1. answers the offer with a local WebRTC peer (aiortc), sends `pre_accept`, waits until media
   can flow, sends `accept` (Graph `POST /{phone-number-id}/calls`),
2. opens the app's call WebSocket (`/api/call/ws`, bearer bridge key, `channel: "whatsapp"`),
   so the call uses the same agent and makes the same report as a browser call,
3. sends the caller's audio to the app as PCM16 24 kHz mono and plays the agent's audio back
   into the call 20 ms at a time at real time (`call_audio.py`),
4. ends the call when the caller hangs up (Meta's `terminate` event) or when the app has saved
   the report or closed the socket and the queued speech has played.

What the main loop uses, without knowing aiortc:

    SUBSCRIBE_FIELD                              the webhook field to add to the subscription
    await enable_calling(client, config)         switch calling on for the number (it stays on)
    await handle_call_event(item, client, config)   one webhook change of field "calls"
    await close_all()                            hang up everything on exit

`client` is the bridge's `httpx.AsyncClient`; `config` is its `Config` (access token, bridge
key, app address, allowed senders, phone number id). The access token goes only to
graph.facebook.com, the bridge key only to the app. A caller's number is never logged and never
reaches the app. aiortc and av load when the first call arrives, not at import.

Optional keys in `config.json`: `call_type_id` (default "turvallisuushavainto"),
`call_language` ("fi", "en" or "sv"; default "en"), `call_ice_servers` (a list of STUN URLs;
default one public STUN server, `[]` for none).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from call_audio import RATE, UPLINK_BYTES, Pacer, Resampler, VoiceBuffer, pcm_frame

logger = logging.getLogger("bridge.calls")

SUBSCRIBE_FIELD = "calls"
GRAPH_HOST = "graph.facebook.com"
CALL_PATH = "/api/call/ws"
DEFAULT_TYPE_ID = "turvallisuushavainto"
DEFAULT_LANGUAGE = "en"
LANGUAGES = ("fi", "en", "sv")
DEFAULT_ICE_SERVERS = ("stun:stun.l.google.com:19302",)
LOCAL_HOSTS = ("localhost", "127.0.0.1")
CONFIG_FILE = Path(__file__).resolve().parent / "config.json"

MEDIA_WAIT_S = 15.0  # Meta wants the answer within 30 to 60 seconds of the offer
OFFER_MAX_AGE_S = 60.0  # an offer that sat in the queue longer is not answered
DRAIN_MAX_S = 30.0  # the longest the last words may take before the call is ended anyway
TAIL_S = 0.4  # after the last frame is handed out: let it through the phone's jitter buffer
MAX_CALLS = 1
UPLINK_QUEUE = 50  # 2 s of caller audio may wait for the app socket; older audio is dropped


# settings --------------------------------------------------------------------------------


def _stored() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def call_settings(config: Any) -> dict[str, Any]:
    """The call's report type, language and ICE servers: an attribute of `config` wins, then
    `config.json`, then the default."""
    stored = _stored()

    def pick(name: str) -> Any:
        value = getattr(config, name, None)
        return stored.get(name) if value is None else value

    language = str(pick("call_language") or DEFAULT_LANGUAGE)
    servers = pick("call_ice_servers")
    if not isinstance(servers, (list, tuple)):
        servers = DEFAULT_ICE_SERVERS
    return {
        "type_id": str(pick("call_type_id") or DEFAULT_TYPE_ID),
        "language": language if language in LANGUAGES else DEFAULT_LANGUAGE,
        "ice_servers": [str(url) for url in servers if str(url).startswith(("stun:", "turn:"))],
    }


def call_ws_url(api_base: str) -> str:
    """`https://host` to `wss://host/api/call/ws`; plain `ws` only for a local app."""
    parts = urlsplit(api_base)
    host = parts.hostname or ""
    if parts.scheme == "https" and host:
        return f"wss://{parts.netloc}{CALL_PATH}"
    if parts.scheme == "http" and host in LOCAL_HOSTS:
        return f"ws://{parts.netloc}{CALL_PATH}"
    raise ValueError("PUBLIC_BASE_URL must be an https origin")


def start_frame(config: Any) -> dict[str, str]:
    settings = call_settings(config)
    return {
        "type": "start",
        "type_id": settings["type_id"],
        "language": settings["language"],
        "channel": "whatsapp",
        "caller_label": "WhatsApp call",
    }


# Graph -----------------------------------------------------------------------------------


async def _graph_post(client: Any, config: Any, what: str, path: str, body: dict[str, Any]) -> bool:
    """One Graph POST with the access token. True on success. Logs the status and Meta's error
    codes only, never the request."""
    parts = urlsplit(str(getattr(config, "graph_url", f"https://{GRAPH_HOST}")))
    if parts.scheme != "https" or (parts.hostname or "").lower() != GRAPH_HOST:
        logger.error("%s not sent: the access token goes only to %s", what, GRAPH_HOST)
        return False
    version = str(getattr(config, "graph_version", "") or "v25.0")
    try:
        response = await client.post(
            f"https://{GRAPH_HOST}/{version}/{path}",
            headers={"Authorization": f"Bearer {config.access_token}"},
            json=body,
            timeout=15,
            follow_redirects=False,
        )
    except Exception as error:  # noqa: BLE001 - httpx errors; the text may hold the URL
        logger.warning("%s failed: %s", what, type(error).__name__)
        return False
    if response.status_code >= 400:
        problem: dict[str, Any] = {}
        with contextlib.suppress(ValueError, AttributeError):
            problem = response.json().get("error") or {}
        logger.warning(
            "%s failed: HTTP %s, code %s, subcode %s",
            what,
            response.status_code,
            problem.get("code"),
            problem.get("error_subcode"),
        )
        return False
    return True


async def enable_calling(client: Any, config: Any) -> bool:
    """Switch calling on for the number. It stays on after the run: WhatsApp clients may take
    a while to show a change of it."""
    settings = {"calling": {"status": "ENABLED", "call_icon_visibility": "DEFAULT"}}
    done = await _graph_post(
        client, config, "enable calling", f"{config.phone_number_id}/settings", settings
    )
    if done:
        logger.info("calling is enabled on the number")
    return done


async def call_action(
    client: Any, config: Any, call_id: str, action: str, sdp: str | None = None
) -> bool:
    """Meta's /calls: pre_accept, accept, reject or terminate a WhatsApp call."""
    body: dict[str, Any] = {"messaging_product": "whatsapp", "call_id": call_id, "action": action}
    if sdp:
        body["session"] = {"sdp_type": "answer", "sdp": sdp}
    return await _graph_post(
        client, config, f"call {action}", f"{config.phone_number_id}/calls", body
    )


# WebRTC (loaded on the first call) -------------------------------------------------------

_rtc_cache: SimpleNamespace | None = None


def _rtc() -> SimpleNamespace:
    """aiortc and the agent's audio track, imported when a call needs them."""
    global _rtc_cache
    if _rtc_cache is None:
        import aiortc
        from aiortc.mediastreams import MediaStreamError

        class AgentTrack(aiortc.MediaStreamTrack):
            """The agent's speech into the call: 20 ms frames at real time, silence while the
            agent is quiet. aiortc's Opus encoder takes the 24 kHz frames up to 48 kHz."""

            kind = "audio"

            def __init__(self) -> None:
                super().__init__()
                self.voice = VoiceBuffer()
                self.pacer = Pacer()

            async def recv(self) -> Any:
                if self.readyState != "live":
                    raise MediaStreamError
                wait = self.pacer.wait()
                if wait > 0:  # real time: the app writes faster than speech plays
                    await asyncio.sleep(wait)
                return pcm_frame(self.voice.next_chunk(), RATE, self.pacer.advance())

        _rtc_cache = SimpleNamespace(
            aiortc=aiortc, AgentTrack=AgentTrack, MediaStreamError=MediaStreamError
        )
    return _rtc_cache


def new_peer(ice_servers: list[str]) -> tuple[Any, Any]:
    """A peer connection with the agent's track on it."""
    rtc = _rtc()
    servers = [rtc.aiortc.RTCIceServer(urls=[url]) for url in ice_servers]
    pc = rtc.aiortc.RTCPeerConnection(rtc.aiortc.RTCConfiguration(iceServers=servers))
    track = rtc.AgentTrack()
    pc.addTrack(track)
    return pc, track


async def answer_offer(pc: Any, offer: str) -> str:
    """Apply WhatsApp's SDP offer and return our answer, ICE candidates included."""
    rtc = _rtc()
    await pc.setRemoteDescription(rtc.aiortc.RTCSessionDescription(sdp=offer, type="offer"))
    await pc.setLocalDescription(await pc.createAnswer())
    return pc.localDescription.sdp


async def _open_app_socket(url: str, bridge_key: str) -> Any:
    from websockets.asyncio.client import connect

    return await connect(
        url,
        additional_headers={"Authorization": f"Bearer {bridge_key}"},
        open_timeout=15,
        max_size=2**20,
    )


# one call --------------------------------------------------------------------------------


@dataclass
class Call:
    call_id: str
    pc: Any
    track: Any
    uplink: asyncio.Queue[bytes] = field(default_factory=lambda: asyncio.Queue(UPLINK_QUEUE))
    ws: Any = None
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    accepted: bool = False
    ended: bool = False
    saved: bool = False
    heard: int = 0  # bytes of caller audio sent to the app

    @property
    def voice(self) -> VoiceBuffer:
        return self.track.voice

    def spawn(self, coroutine: Any) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task


class CallManager:
    """Every call in progress, keyed by Meta's call id."""

    def __init__(self, client: Any, config: Any) -> None:
        self.client = client
        self.config = config
        self.calls: dict[str, Call] = {}
        self.answered: list[str] = []  # call ids already taken: Meta may deliver an event twice
        self.background: set[asyncio.Task[Any]] = set()

    async def signal(self, call_id: str, action: str, sdp: str | None = None) -> bool:
        return await call_action(self.client, self.config, call_id, action, sdp)

    # webhook ----------------------------------------------------------------------------

    async def handle(self, item: dict[str, Any]) -> int:
        """One webhook change of field `calls` (the change, or its `value`). Returns how many
        calls were taken. Returns at once: a call connects and runs in the background."""
        value = item.get("value") if isinstance(item.get("value"), dict) else item
        number_id = str((value.get("metadata") or {}).get("phone_number_id") or "")
        if number_id and number_id != str(self.config.phone_number_id):
            return 0
        contact = (value.get("contacts") or [{}])[0] or {}
        taken = 0
        for event in value.get("calls") or []:
            if not isinstance(event, dict):
                continue
            call_id = str(event.get("id") or "")
            kind = event.get("event")
            if not call_id:
                continue
            if kind == "terminate":  # the caller hung up, or the call ended on Meta's side
                await self.end(call_id, tell_meta=False)
                continue
            session = event.get("session") or {}
            if kind != "connect" or session.get("sdp_type") != "offer" or not session.get("sdp"):
                continue
            if call_id in self.calls or call_id in self.answered:
                continue
            self.answered = [*self.answered[-99:], call_id]
            caller = "".join(
                c for c in str(contact.get("wa_id") or event.get("from") or "") if c.isdigit()
            )
            if caller not in self.config.allowed:
                logger.info("call rejected: the caller is not allowed")
                await self.signal(call_id, "reject")
                continue
            if _age(event.get("timestamp")) > OFFER_MAX_AGE_S:
                logger.info("call %s ignored: the offer is too old", call_id)
                continue
            if len(self.calls) >= MAX_CALLS:
                logger.info("call %s rejected: another call is in progress", call_id)
                await self.signal(call_id, "reject")
                continue
            task = asyncio.ensure_future(self.connect(call_id, str(session["sdp"])))
            self.background.add(task)
            task.add_done_callback(self.background.discard)
            taken += 1
        return taken

    # connect ----------------------------------------------------------------------------

    async def connect(self, call_id: str, offer: str) -> bool:
        try:
            return await self._connect(call_id, offer)
        except Exception as error:  # noqa: BLE001 - one call must not stop the bridge
            logger.error("call %s failed: %s", call_id, type(error).__name__)
            await self.end(call_id, tell_meta=True)
            return False

    async def _connect(self, call_id: str, offer: str) -> bool:
        rtc = _rtc()
        pc, track = new_peer(call_settings(self.config)["ice_servers"])
        call = Call(call_id, pc, track)
        self.calls[call_id] = call

        @pc.on("track")
        def on_track(remote: Any) -> None:
            if remote.kind == "audio":
                call.spawn(self._listen(call, remote, rtc.MediaStreamError))

        @pc.on("connectionstatechange")
        async def on_state() -> None:
            if call.accepted and pc.connectionState in ("failed", "closed"):
                await self.end(call_id, tell_meta=True)  # the media path is gone

        answer = await answer_offer(pc, offer)
        # Pre-accepting lets the media connect first, so the caller hears the first words.
        if not await self.signal(call_id, "pre_accept", answer):
            await self.end(call_id, tell_meta=False)
            return False
        deadline = time.monotonic() + MEDIA_WAIT_S
        while pc.connectionState not in ("connected", "failed", "closed"):
            if call.ended or time.monotonic() > deadline:
                break
            await asyncio.sleep(0.1)
        if call.ended:  # the caller gave up while it was connecting
            return False
        if pc.connectionState != "connected":
            logger.warning("call %s: media did not connect (%s)", call_id, pc.connectionState)
            await self.end(call_id, tell_meta=True)
            return False
        if not await self.signal(call_id, "accept", answer):
            await self.end(call_id, tell_meta=False)
            return False
        if call.ended:
            return False
        call.accepted = True
        logger.info("call %s: connected", call_id)
        call.spawn(self._app(call))
        return True

    # audio ------------------------------------------------------------------------------

    async def _listen(self, call: Call, remote: Any, stream_error: type[Exception]) -> None:
        """The caller's audio: Opus decoded by aiortc, to PCM16 mono 24 kHz in 40 ms pieces."""
        resampler = Resampler(RATE)
        pending = bytearray()
        while True:
            try:
                frame = await remote.recv()
            except stream_error:
                return
            pending += resampler.feed(frame)
            while len(pending) >= UPLINK_BYTES:
                if call.uplink.full():
                    call.uplink.get_nowait()  # the app is not listening yet: keep the newest
                call.uplink.put_nowait(bytes(pending[:UPLINK_BYTES]))
                del pending[:UPLINK_BYTES]

    async def _uplink(self, call: Call, ws: Any) -> None:
        while True:
            piece = await call.uplink.get()
            await ws.send(piece)
            call.heard += len(piece)

    async def _app(self, call: Call) -> None:
        """The app's side of the call: its call WebSocket, as the browser uses it."""
        sender: asyncio.Task[Any] | None = None
        try:
            ws = await _open_app_socket(
                call_ws_url(self.config.api_base), str(self.config.bridge_key)
            )
            call.ws = ws
            if call.ended:  # hung up while the socket was opening
                await ws.close()
                return
            await ws.send(json.dumps(start_frame(self.config)))
            sender = call.spawn(self._uplink(call, ws))
            async for message in ws:
                if isinstance(message, bytes | bytearray):
                    call.voice.write(bytes(message))  # the agent's speech
                    continue
                frame = _parse(message)
                kind = frame.get("type")
                if kind == "interrupt":  # the caller spoke over the agent
                    call.voice.clear()
                elif kind == "saved":
                    call.saved = True
                    logger.info("call %s: the report was saved", call.call_id)
                elif kind == "ready":
                    logger.info("call %s: the agent is ready", call.call_id)
                elif kind == "error":
                    logger.warning("call %s: the app reported an error", call.call_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - also a socket closed without a close frame
            logger.warning("call %s: app socket: %s", call.call_id, type(error).__name__)
        finally:
            if sender is not None:
                sender.cancel()
        if call.ended:
            return
        # The app is done (saved, or the socket closed): play what is queued, then hang up.
        deadline = time.monotonic() + DRAIN_MAX_S
        while not call.voice.drained and not call.ended and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await asyncio.sleep(TAIL_S)
        await self.end(call.call_id, tell_meta=True)

    # end --------------------------------------------------------------------------------

    async def end(self, call_id: str, *, tell_meta: bool) -> None:
        """Close one call: Meta (when we end it), the app socket, the tasks and the peer."""
        call = self.calls.pop(call_id, None)
        if call is None or call.ended:
            return
        call.ended = True
        if tell_meta:
            await self.signal(call_id, "terminate")
        if call.ws is not None:
            with contextlib.suppress(Exception):
                await call.ws.send(json.dumps({"type": "stop"}))
            with contextlib.suppress(Exception):
                await call.ws.close()
        current = asyncio.current_task()
        for task in list(call.tasks):
            if task is not current:
                task.cancel()
        with contextlib.suppress(Exception):
            await call.pc.close()
        logger.info(
            "call %s: ended after %.0f s, %.0f s heard, %d underruns%s",
            call_id,
            call.track.pacer.seconds,
            call.heard / (RATE * 2),
            call.voice.underruns,
            ", report saved" if call.saved else "",
        )

    async def close(self) -> None:
        for task in list(self.background):
            task.cancel()
        for call_id in list(self.calls):
            await self.end(call_id, tell_meta=True)


def _parse(raw: object) -> dict[str, Any]:
    try:
        data = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _age(timestamp: object) -> float:
    """Seconds since Meta's event timestamp (unix seconds); 0 when it cannot be read."""
    try:
        return time.time() - float(str(timestamp))
    except ValueError:
        return 0.0


# what the main loop calls ----------------------------------------------------------------

_manager: CallManager | None = None


async def handle_call_event(item: dict[str, Any], client: Any, config: Any) -> int:
    """Route one webhook change of field `calls`. The delivery's signature and the phone
    number id are the caller's to check, as for messages; the sender is checked here."""
    global _manager
    if _manager is None or _manager.client is not client or _manager.config is not config:
        if _manager is not None:
            await _manager.close()
        _manager = CallManager(client, config)
    return await _manager.handle(item)


async def close_all() -> None:
    """Hang up every call in progress. Safe to call when no call was ever taken."""
    global _manager
    manager, _manager = _manager, None
    if manager is not None:
        await manager.close()
