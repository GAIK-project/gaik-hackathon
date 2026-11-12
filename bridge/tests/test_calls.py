"""WhatsApp calls: a local WebRTC peer stands in for WhatsApp, an httpx mock for Meta's Graph
API and a local WebSocket server for the app's call socket. Nothing here reaches the network
beyond 127.0.0.1, and no real call is placed or answered."""

from __future__ import annotations

import array
import asyncio
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx

BRIDGE = Path(__file__).resolve().parent.parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import call_audio  # noqa: E402
import calls  # noqa: E402
from call_audio import FRAME, FRAME_BYTES, PREBUFFER, RATE, Pacer, Resampler, VoiceBuffer  # noqa: E402

CALLER = "358401234567"
LEVEL = 8000


# helpers ---------------------------------------------------------------------------------


def tone(rate: int, seconds: float, hz: float = 440.0, start: int = 0) -> bytes:
    count = int(rate * seconds)
    samples = (int(LEVEL * math.sin(2 * math.pi * hz * (start + i) / rate)) for i in range(count))
    return array.array("h", samples).tobytes()


def samples_of(pcm: bytes) -> array.array:
    values = array.array("h")
    values.frombytes(pcm[: len(pcm) // 2 * 2])
    return values


def rms(pcm: bytes) -> float:
    values = samples_of(pcm)
    return math.sqrt(sum(v * v for v in values) / max(1, len(values)))


def pitch(pcm: bytes, rate: int) -> float:
    """The tone's frequency from its zero crossings."""
    values = samples_of(pcm)
    crossings = sum(1 for a, b in zip(values, values[1:], strict=False) if (a < 0) != (b < 0))
    return crossings / 2 / (len(values) / rate)


def config(port: int = 1, **more) -> SimpleNamespace:
    values = {
        "access_token": "test-access-token",
        "bridge_key": "test-bridge-key",
        "api_base": f"http://127.0.0.1:{port}",
        "allowed": frozenset({CALLER}),
        "phone_number_id": "111",
        "graph_version": "v25.0",
        "graph_url": "https://graph.facebook.com",
        "call_ice_servers": [],  # host candidates are enough on one machine; no STUN from a test
    }
    return SimpleNamespace(**{**values, **more})


def connect_event(call_id: str, sdp: str, caller: str = CALLER) -> dict:
    return {
        "field": "calls",
        "value": {
            "messaging_product": "whatsapp",
            "metadata": {"phone_number_id": "111"},
            "contacts": [{"wa_id": caller}],
            "calls": [
                {
                    "id": call_id,
                    "from": caller,
                    "event": "connect",
                    "timestamp": str(int(time.time())),
                    "direction": "USER_INITIATED",
                    "session": {"sdp_type": "offer", "sdp": sdp},
                }
            ],
        },
    }


def terminate_event(call_id: str) -> dict:
    return {
        "field": "calls",
        "value": {
            "metadata": {"phone_number_id": "111"},
            "calls": [{"id": call_id, "from": CALLER, "event": "terminate", "status": "COMPLETED"}],
        },
    }


class FakeGraph:
    """Meta's Graph API: records what the bridge posts; on `pre_accept` the phone applies the
    answer, as WhatsApp does on its side."""

    def __init__(self, phone=None) -> None:
        self.phone = phone
        self.requests: list[tuple[str, str, dict, str]] = []
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    @property
    def actions(self) -> list[str]:
        return [body["action"] for _, path, body, _ in self.requests if path.endswith("/calls")]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        auth = request.headers.get("authorization", "")
        self.requests.append((request.url.host, request.url.path, body, auth))
        if body.get("action") == "pre_accept" and self.phone is not None:
            from aiortc import RTCSessionDescription

            await self.phone.setRemoteDescription(
                RTCSessionDescription(sdp=body["session"]["sdp"], type="answer")
            )
        return httpx.Response(200, json={"success": True})


class FakeApp:
    """The app's `/api/call/ws`: checks the handshake, keeps what it hears, and lets the test
    speak through `self.ws`."""

    def __init__(self) -> None:
        self.auth: str | None = None
        self.path: str | None = None
        self.start: dict | None = None
        self.heard = bytearray()
        self.texts: list[dict] = []
        self.ws = None
        self.ready = asyncio.Event()
        self.closed = asyncio.Event()
        self.server = None
        self.port = 0

    async def open(self) -> FakeApp:
        from websockets.asyncio.server import serve

        self.server = await serve(self.handler, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def handler(self, ws) -> None:
        self.auth = ws.request.headers.get("Authorization")
        self.path = ws.request.path
        self.start = json.loads(await ws.recv())
        self.ws = ws
        await ws.send(json.dumps({"type": "ready", "report_id": "r1", "report_type": {}}))
        self.ready.set()
        try:
            async for message in ws:
                if isinstance(message, bytes):
                    self.heard += message
                else:
                    self.texts.append(json.loads(message))
        finally:
            self.closed.set()

    async def close(self) -> None:
        self.server.close()
        await self.server.wait_closed()


def make_phone():
    """The caller's phone: sends a 440 Hz tone at 48 kHz and notes how loud each frame it
    receives is."""
    from aiortc import MediaStreamTrack, RTCPeerConnection
    from aiortc.mediastreams import MediaStreamError

    class Microphone(MediaStreamTrack):
        kind = "audio"

        def __init__(self) -> None:
            super().__init__()
            self.sent = 0

        async def recv(self):
            await asyncio.sleep(0.02)
            frame = call_audio.pcm_frame(tone(48000, 0.02, start=self.sent), 48000, self.sent)
            self.sent += 960
            return frame

    phone = RTCPeerConnection()
    phone.addTrack(Microphone())
    loud: list[tuple[float, int]] = []  # (time, peak) of every frame the phone plays

    @phone.on("track")
    def on_track(track) -> None:
        async def read() -> None:
            while True:
                try:
                    frame = await track.recv()
                except MediaStreamError:
                    return
                peak = max((abs(v) for v in samples_of(bytes(frame.planes[0]))), default=0)
                loud.append((time.monotonic(), peak))

        asyncio.ensure_future(read())

    return phone, loud


async def until(condition, seconds: float = 15.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        await asyncio.sleep(0.05)
    return condition()


async def start_call(call_id: str = "wacid.test"):
    app = await FakeApp().open()
    phone, loud = make_phone()
    await phone.setLocalDescription(await phone.createOffer())
    graph = FakeGraph(phone)
    cfg = config(app.port)
    taken = await calls.handle_call_event(
        connect_event(call_id, phone.localDescription.sdp), graph.client, cfg
    )
    assert taken == 1
    assert await until(app.ready.is_set), f"the call never reached the app: {graph.actions}"
    return app, phone, loud, graph, cfg


async def finish(app: FakeApp, phone, graph: FakeGraph) -> None:
    await calls.close_all()
    await phone.close()
    await app.close()
    await graph.client.aclose()


# the audio core --------------------------------------------------------------------------


def test_caller_audio_48k_to_24k_keeps_duration_level_and_pitch():
    resampler = Resampler(RATE)
    out = bytearray()
    for n in range(50):  # one second in 20 ms frames, as the Opus decoder gives them
        out += resampler.feed_pcm(tone(48000, 0.02, start=n * 960), 48000)
    out += resampler.flush()
    assert abs(len(out) // 2 - RATE) <= RATE * 0.02  # one second stays one second
    assert abs(rms(bytes(out)) - LEVEL / math.sqrt(2)) < LEVEL * 0.05
    assert abs(pitch(bytes(out), RATE) - 440) < 5


def test_agent_audio_24k_to_48k_keeps_duration_level_and_pitch():
    resampler = Resampler(48000)  # what aiortc's Opus encoder does with the track's frames
    out = bytearray()
    for n in range(50):
        out += resampler.feed_pcm(tone(RATE, 0.02, start=n * FRAME), RATE)
    out += resampler.flush()
    assert abs(len(out) // 2 - 48000) <= 48000 * 0.02
    assert abs(rms(bytes(out)) - LEVEL / math.sqrt(2)) < LEVEL * 0.05
    assert abs(pitch(bytes(out), 48000) - 440) < 5


def test_stereo_input_becomes_mono():
    from av import AudioFrame

    mono = samples_of(tone(48000, 0.02))
    stereo = array.array("h", (v for sample in mono for v in (sample, sample)))
    frame = AudioFrame(format="s16", layout="stereo", samples=960)
    frame.planes[0].update(stereo.tobytes())
    frame.sample_rate = 48000
    resampler = Resampler(RATE)
    out = resampler.feed(frame) + resampler.flush()
    assert abs(len(out) // 2 - FRAME) <= 40
    assert abs(rms(out) - LEVEL / math.sqrt(2)) < LEVEL * 0.15  # a single short frame


def test_the_agent_speaks_only_whole_frames_once_a_quarter_second_is_ready():
    now = [100.0]
    voice = VoiceBuffer(clock=lambda: now[0])
    voice.write(b"\x01\x00" * FRAME)  # 20 ms: not enough to start
    assert voice.next_chunk() == bytes(FRAME_BYTES)
    voice.write(b"\x01\x00" * (PREBUFFER // 2))
    assert voice.next_chunk() != bytes(FRAME_BYTES)  # a quarter second is there: speech starts
    while len(voice.buffer) >= FRAME_BYTES:
        voice.next_chunk()
    voice.write(b"\x01\x00" * 10)  # still less than a frame, and more is coming
    left = len(voice.buffer)
    assert 0 < left < FRAME_BYTES
    assert voice.next_chunk() == bytes(FRAME_BYTES) and voice.underruns == 1  # waits, not cut
    now[0] += 0.2  # the writer has stopped: the rest goes out, padded at the very end
    last = voice.next_chunk()
    assert last[:left] == b"\x01\x00" * (left // 2) and last[left:] == bytes(FRAME_BYTES - left)
    assert voice.drained and not voice.playing


def test_an_interrupt_drops_the_queued_speech():
    voice = VoiceBuffer()
    voice.write(tone(RATE, 2.0))
    assert voice.next_chunk() != bytes(FRAME_BYTES)
    voice.clear()
    assert voice.drained and voice.next_chunk() == bytes(FRAME_BYTES)
    assert voice.dropped == RATE * 2 * 2 - FRAME_BYTES


def test_all_queued_speech_comes_out_in_order():
    now = [0.0]
    voice = VoiceBuffer(clock=lambda: now[0])
    speech = tone(RATE, 1.0)
    voice.write(speech)
    now[0] += 1.0
    out = bytearray()
    while not voice.drained:
        out += voice.next_chunk()
    assert bytes(out[: len(speech)]) == speech and len(out) == len(speech)  # 50 whole frames


def test_frames_are_due_at_real_time_without_drift():
    now = [50.0]
    pacer = Pacer(clock=lambda: now[0])
    assert pacer.wait() == 0.0  # the first frame goes at once
    pacer.advance()
    for n in range(1, 1000):
        wait = pacer.wait()
        assert abs(wait - 0.02) < 1e-6
        now[0] += wait  # sleep exactly as long as asked
        assert pacer.advance() == n * FRAME
    assert abs((now[0] - 50.0) - 999 * 0.02) < 1e-6 and abs(pacer.seconds - 20.0) < 1e-9
    now[0] += 5.0  # the loop stalled: no negative wait, the frames catch up
    assert pacer.wait() == 0.0


def test_the_track_hands_out_frames_at_real_time():
    async def run():
        _, track = calls.new_peer([])
        track.voice.write(tone(RATE, 1.0))
        started = time.monotonic()
        frames = [await track.recv() for _ in range(26)]
        return time.monotonic() - started, frames

    seconds, frames = asyncio.run(run())
    assert 0.45 <= seconds <= 0.75  # 25 intervals of 20 ms
    assert all(f.samples == FRAME and f.sample_rate == RATE for f in frames)
    assert [f.pts for f in frames] == [n * FRAME for n in range(26)]
    assert rms(b"".join(bytes(f.planes[0]) for f in frames)) > LEVEL / 2


# signalling ------------------------------------------------------------------------------

# Meta's sample offer for a user-initiated call (Calling API reference, "SDP overview and sample
# SDP structures"): ICE-lite media server, Opus 111, telephone-event 126.
WHATSAPP_OFFER = """v=0
o=- 7602563789789945080 2 IN IP4 127.0.0.1
s=-
t=0 0
a=group:BUNDLE audio
a=msid-semantic: WMS 6932bc1c-db1a-4abe-b437-0c4168be8a13
a=ice-lite
m=audio 40012 UDP/TLS/RTP/SAVPF 111 126
c=IN IP4 31.13.65.60
a=rtcp:9 IN IP4 0.0.0.0
a=candidate:1972637320 1 udp 2113937151 31.13.65.60 40012 typ host generation 0 network-cost 50
a=ice-ufrag:6k2qP1R6kBfI/2
a=ice-pwd:UApvJw3NcwFRDvIMKdM0vWCdlXah25E9
a=fingerprint:sha-256 1B:B6:6B:40:A5:0B:8C:75:0D:8C:CB:90:2F:99:74:1E:26:45:AE:AF:45:C1:51:60:8F:73:C9:2D:10:6D:8A:88
a=setup:actpass
a=mid:audio
a=sendrecv
a=rtcp-mux
a=rtpmap:111 opus/48000/2
a=rtcp-fb:111 transport-cc
a=fmtp:111 minptime=10;useinbandfec=1
a=rtpmap:126 telephone-event/8000
a=ssrc:4208138518 cname:gAXq2V9TKltrnapv
""".replace("\n", "\r\n")


def test_the_answer_to_whatsapps_sample_offer():
    async def answer():
        pc, _ = calls.new_peer([])
        sdp = await calls.answer_offer(pc, WHATSAPP_OFFER)
        role = pc.getTransceivers()[0].receiver.transport.transport.role
        await pc.close()
        return sdp, role

    sdp, role = asyncio.run(answer())
    assert "a=rtpmap:111 opus/48000/2" in sdp and "a=mid:audio" in sdp
    assert "a=setup:active" in sdp and "a=fingerprint:sha-256" in sdp and "a=candidate:" in sdp
    assert "a=sendrecv" in sdp
    assert role == "controlling"  # a full ICE agent leads against WhatsApp's ICE-lite server


def test_enabling_calling_posts_the_settings_to_graph_only():
    async def run():
        graph = FakeGraph()
        done = await calls.enable_calling(graph.client, config())
        refused = await calls.enable_calling(
            graph.client, config(graph_url="https://graph.example.test")
        )
        return done, refused, graph.requests

    done, refused, requests = asyncio.run(run())
    assert done and not refused and len(requests) == 1  # the token never left for another host
    host, path, body, auth = requests[0]
    assert (host, path) == ("graph.facebook.com", "/v25.0/111/settings")
    assert body == {"calling": {"status": "ENABLED", "call_icon_visibility": "DEFAULT"}}
    assert auth == "Bearer test-access-token"


def test_the_start_frame_and_the_socket_address(monkeypatch):
    monkeypatch.setattr(calls, "CONFIG_FILE", BRIDGE / "no-such-config.json")
    assert calls.start_frame(SimpleNamespace()) == {
        "type": "start",
        "type_id": "turvallisuushavainto",
        "language": "en",
        "channel": "whatsapp",
        "caller_label": "WhatsApp call",
    }
    chosen = calls.start_frame(SimpleNamespace(call_type_id="laatupoikkeama", call_language="fi"))
    assert (chosen["type_id"], chosen["language"]) == ("laatupoikkeama", "fi")
    assert calls.start_frame(SimpleNamespace(call_language="xx"))["language"] == "en"
    assert calls.call_ws_url("https://app.example.test") == "wss://app.example.test/api/call/ws"
    assert calls.call_ws_url("http://127.0.0.1:8000") == "ws://127.0.0.1:8000/api/call/ws"
    for bad in ("http://app.example.test", "ftp://x", ""):
        try:
            calls.call_ws_url(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} was accepted")


def test_importing_the_module_does_not_load_aiortc():
    code = (
        "import sys; import calls; "
        "print(any(m in sys.modules for m in ('aiortc', 'av', 'websockets')))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=BRIDGE, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "False"


def test_calls_from_numbers_not_allowed_are_rejected_and_old_offers_ignored():
    async def run():
        graph = FakeGraph()
        cfg = config()
        stranger = await calls.handle_call_event(
            connect_event("wacid.x", "v=0", caller="358409999999"), graph.client, cfg
        )
        old = connect_event("wacid.old", "v=0")
        old["value"]["calls"][0]["timestamp"] = str(int(time.time()) - 600)
        stale = await calls.handle_call_event(old, graph.client, cfg)
        other = connect_event("wacid.other", "v=0")
        other["value"]["metadata"]["phone_number_id"] = "222"
        foreign = await calls.handle_call_event(other, graph.client, cfg)
        await calls.close_all()
        return stranger, stale, foreign, graph.actions

    stranger, stale, foreign, actions = asyncio.run(run())
    assert (stranger, stale, foreign) == (0, 0, 0)
    assert actions == ["reject"]  # the stranger only; nothing was answered


# a whole call ----------------------------------------------------------------------------


def test_a_call_connects_audio_flows_both_ways_and_the_app_ends_it():
    async def call():
        app, phone, loud, graph, cfg = await start_call()
        again = await calls.handle_call_event(
            connect_event("wacid.test", phone.localDescription.sdp), graph.client, cfg
        )
        manager = calls._manager
        live = manager.calls["wacid.test"]

        # the caller's voice reaches the app
        assert await until(lambda: len(app.heard) > RATE)  # half a second of PCM16
        heard = bytes(app.heard)

        # the agent's voice reaches the caller
        await app.ws.send(tone(RATE, 1.0, hz=300))
        assert await until(lambda: any(peak > 1000 for _, peak in loud))

        # an interrupt drops what is queued: five seconds were waiting, silence follows
        await asyncio.sleep(1.3)  # the first second has played out
        await app.ws.send(tone(RATE, 5.0, hz=300))
        await asyncio.sleep(0.6)
        speaking = max(peak for at, peak in loud if at > time.monotonic() - 0.3)
        await app.ws.send(json.dumps({"type": "interrupt"}))
        await asyncio.sleep(0.2)
        left = len(live.voice.buffer)
        dropped = live.voice.dropped
        cut = time.monotonic()
        await asyncio.sleep(1.2)
        after = [peak for at, peak in loud if at > cut + 0.6]

        # the app saves the report and closes: the last words play, then the bridge hangs up
        await app.ws.send(tone(RATE, 0.6, hz=300))
        await app.ws.send(json.dumps({"type": "saved", "report_id": "r1"}))
        await app.ws.close()
        closing = time.monotonic()
        assert await until(lambda: "terminate" in graph.actions)
        hung_up = time.monotonic() - closing
        last_words = [peak for at, peak in loud if at > closing]
        state = live.pc.connectionState
        result = SimpleNamespace(
            again=again,
            heard=heard,
            speaking=speaking,
            left=left,
            dropped=dropped,
            after=after,
            hung_up=hung_up,
            last_words=last_words,
            state=state,
            actions=list(graph.actions),
            open_calls=len(manager.calls),
            saved=live.saved,
            tasks=[t for t in live.tasks if not t.done()],
            app=app,
            graph=graph,
        )
        await asyncio.sleep(0.1)
        result.tasks = [t for t in live.tasks if not t.done()]
        await finish(app, phone, graph)
        return result

    r = asyncio.run(call())
    assert r.again == 0  # Meta delivering the same connect twice does not start a second call
    assert r.actions == ["pre_accept", "accept", "terminate"]
    assert all(host == "graph.facebook.com" for host, *_ in r.graph.requests)
    assert all(path == "/v25.0/111/calls" for _, path, *_ in r.graph.requests)
    answer = r.graph.requests[0][2]["session"]
    assert answer["sdp_type"] == "answer" and "a=setup:active" in answer["sdp"]
    # the handshake with the app
    assert r.app.auth == "Bearer test-bridge-key" and r.app.path == "/api/call/ws"
    assert r.app.start["type"] == "start" and r.app.start["channel"] == "whatsapp"
    assert r.app.start["caller_label"] == "WhatsApp call"
    # caller to app: the 440 Hz tone, at 24 kHz, at a sane level (Opus is lossy)
    assert len(r.heard) % 2 == 0
    body = r.heard[len(r.heard) // 2 :]  # skip the codec's first moments
    assert abs(pitch(body, RATE) - 440) < 15 and rms(body) > LEVEL / 4
    # app to caller, then the interrupt
    assert r.speaking > 1000
    assert r.left == 0 and r.dropped > RATE * 2 * 3  # more than three seconds thrown away
    assert r.after and max(r.after) < 300  # silence instead of the rest of the five seconds
    # the end: the queued speech played first, and everything is closed
    assert max(r.last_words, default=0) > 1000 and r.hung_up >= 0.5
    assert r.saved and r.state == "closed" and r.open_calls == 0 and not r.tasks


def test_the_caller_hangs_up_and_everything_is_closed():
    async def call():
        app, phone, _, graph, cfg = await start_call("wacid.hangup")
        manager = calls._manager
        live = manager.calls["wacid.hangup"]
        await app.ws.send(tone(RATE, 3.0, hz=300))  # the agent is mid-sentence
        assert await until(lambda: len(app.heard) > 0)
        await calls.handle_call_event(terminate_event("wacid.hangup"), graph.client, cfg)
        closed = await until(app.closed.is_set, 5)
        await asyncio.sleep(0.1)
        result = SimpleNamespace(
            closed=closed,
            texts=list(app.texts),
            actions=list(graph.actions),
            state=live.pc.connectionState,
            open_calls=len(manager.calls),
            tasks=[t for t in live.tasks if not t.done()],
        )
        await finish(app, phone, graph)
        return result

    r = asyncio.run(call())
    assert r.actions == ["pre_accept", "accept"]  # no terminate back to Meta: it already ended
    assert r.closed and {"type": "stop"} in r.texts  # the app is told, so it settles the report
    assert r.state == "closed" and r.open_calls == 0 and not r.tasks


def test_closing_the_bridge_hangs_up_a_running_call():
    async def call():
        app, phone, _, graph, _cfg = await start_call("wacid.exit")
        live = calls._manager.calls["wacid.exit"]
        await calls.close_all()
        closed = await until(app.closed.is_set, 5)
        result = (closed, list(graph.actions), live.pc.connectionState, calls._manager)
        await finish(app, phone, graph)
        return result

    closed, actions, state, manager = asyncio.run(call())
    assert closed and actions == ["pre_accept", "accept", "terminate"]
    assert state == "closed" and manager is None


def test_a_call_whose_media_never_connects_is_given_up(monkeypatch):
    monkeypatch.setattr(calls, "MEDIA_WAIT_S", 1.0)

    async def call():
        graph = FakeGraph()  # no phone: nobody applies the answer, so no media
        cfg = config()
        await calls.handle_call_event(
            connect_event("wacid.dead", WHATSAPP_OFFER), graph.client, cfg
        )
        manager = calls._manager
        done = await until(lambda: "terminate" in graph.actions, 10)
        await asyncio.sleep(0.1)
        result = (done, list(graph.actions), len(manager.calls))
        await calls.close_all()
        await graph.client.aclose()
        return result

    done, actions, open_calls = asyncio.run(call())
    assert done and actions == ["pre_accept", "terminate"] and open_calls == 0


# the hook in main.py ---------------------------------------------------------------------


def test_the_bridge_routes_calls_only_with_the_flag(monkeypatch, tmp_path):
    import base64

    import main

    cfg = main.Config(
        access_token="test-access-token",
        app_secret="test-app-secret",
        bridge_key="test-bridge-key",
        api_base="http://127.0.0.1:1",
        allowed=frozenset({CALLER}),
        phone_number_id="111",
        waba_id="222",
        state_file=tmp_path / "seen.json",
    )
    routed: list[dict] = []

    async def fake_handle(item, client, config):
        routed.append(item)
        return 1

    monkeypatch.setattr(calls, "handle_call_event", fake_handle)
    ours = connect_event("wacid.hook", "v=0")
    theirs = connect_event("wacid.other", "v=0")
    theirs["value"]["metadata"]["phone_number_id"] = "999"
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [ours, theirs]}]}
    body = json.dumps(payload).encode()
    item = {
        "body_b64": base64.b64encode(body).decode(),
        "signature": main.sign(cfg.app_secret, body),
    }

    async def run(flag: bool, signature: str | None = None):
        routed.clear()
        graph = SimpleNamespace(client=object())
        bridge = main.Bridge(cfg, graph, api=None)
        bridge.calls = flag
        bridge.handle_delivery({**item, "signature": signature or item["signature"]})
        await asyncio.sleep(0.05)
        return list(routed)

    assert asyncio.run(run(False)) == []  # without --calls a call is not touched
    assert asyncio.run(run(True, "sha256=" + "0" * 64)) == []  # a wrong signature routes nothing
    with_flag = asyncio.run(run(True))
    assert [value["calls"][0]["id"] for value in with_flag] == ["wacid.hook"]  # our number only
