"""The audio core of a WhatsApp call: resampling and real-time pacing, no WebRTC in it.

The app's call WebSocket speaks PCM16 little-endian mono at 24 kHz (docs/ARCHITECTURE.md 8.2).
WhatsApp's media is Opus at 48 kHz. Three small pieces sit between them:

- `Resampler`: any decoded audio frame (or raw PCM16) to PCM16 mono at one rate, with `av`;
- `VoiceBuffer`: the agent's speech queued for the call. It starts once a quarter second is
  buffered and never pads a frame with silence in the middle of speech; a frame that had to
  wait is counted as an underrun (half-empty frames made an earlier prototype break up);
- `Pacer`: when the next 20 ms frame is due, so speech leaves at real time although the app
  writes it faster than it plays.

`av` is imported only when a `Resampler` or a frame is made.
"""

from __future__ import annotations

import fractions
import time
from collections.abc import Callable
from typing import Any

RATE = 24000  # the app's call WebSocket: PCM16 mono
FRAME = 480  # samples in 20 ms at RATE
FRAME_BYTES = FRAME * 2
PREBUFFER = RATE // 4 * 2  # bytes: a quarter second before the agent starts speaking
PAUSE_S = 0.15  # no new speech for this long: the reply is done, play what is left
UPLINK_BYTES = RATE // 25 * 2  # caller audio goes to the app in 40 ms pieces


class VoiceBuffer:
    """The agent's speech on its way into the call, handed out 20 ms at a time."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.buffer = bytearray()
        self.playing = False
        self.last_write = float("-inf")
        self.underruns = 0  # frames that waited for speech mid-sentence, logged per call
        self.dropped = 0  # bytes thrown away by an interrupt

    def write(self, pcm: bytes) -> None:
        self.buffer += pcm
        self.last_write = self.clock()

    def clear(self) -> None:
        """The caller spoke over the agent: nothing queued is played."""
        self.dropped += len(self.buffer)
        self.buffer.clear()
        self.playing = False

    @property
    def drained(self) -> bool:
        return not self.buffer

    def next_chunk(self) -> bytes:
        """The next 20 ms: speech when it is ready, else silence."""
        paused = self.clock() - self.last_write > PAUSE_S  # the writer has finished
        if not self.playing and (len(self.buffer) >= PREBUFFER or (self.buffer and paused)):
            self.playing = True
        if not self.playing:
            return bytes(FRAME_BYTES)
        if len(self.buffer) >= FRAME_BYTES or paused:
            chunk = bytes(self.buffer[:FRAME_BYTES]).ljust(FRAME_BYTES, b"\0")  # only at the end
            del self.buffer[:FRAME_BYTES]
            if not self.buffer:
                self.playing = False
            return chunk
        self.underruns += 1  # speech is still coming: wait one frame rather than cut a word
        return bytes(FRAME_BYTES)


class Pacer:
    """Real time for outgoing frames: frame n is due n * 20 ms after the first one."""

    def __init__(
        self, rate: int = RATE, frame: int = FRAME, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.rate = rate
        self.frame = frame
        self.clock = clock
        self.started: float | None = None
        self.sent = 0  # samples handed out so far; also the next frame's pts

    def wait(self) -> float:
        """Seconds until the next frame is due (0 for the first, or when running late)."""
        if self.started is None:
            self.started = self.clock()
            return 0.0
        return max(0.0, self.started + self.sent / self.rate - self.clock())

    def advance(self) -> int:
        """Count one frame as sent; returns its pts."""
        pts = self.sent
        self.sent += self.frame
        return pts

    @property
    def seconds(self) -> float:
        return self.sent / self.rate


def pcm_frame(pcm: bytes, rate: int = RATE, pts: int = 0) -> Any:
    """PCM16 mono bytes as an `av.AudioFrame`."""
    from av import AudioFrame

    frame = AudioFrame(format="s16", layout="mono", samples=len(pcm) // 2)
    frame.planes[0].update(pcm)
    frame.sample_rate = rate
    frame.pts = pts
    frame.time_base = fractions.Fraction(1, rate)
    return frame


class Resampler:
    """Audio frames of any rate and layout to PCM16 mono at `rate`. Keeps its filter state, so
    feed one stream's frames in order."""

    def __init__(self, rate: int = RATE) -> None:
        from av import AudioResampler

        self.rate = rate
        self._resampler = AudioResampler(format="s16", layout="mono", rate=rate)
        self._pts = 0

    def feed(self, frame: Any) -> bytes:
        out = bytearray()
        for piece in self._resampler.resample(frame):
            out += bytes(piece.planes[0])[: piece.samples * 2]
        return bytes(out)

    def feed_pcm(self, pcm: bytes, rate: int) -> bytes:
        frame = pcm_frame(pcm, rate, self._pts)
        self._pts += len(pcm) // 2
        return self.feed(frame)

    def flush(self) -> bytes:
        """What the filter still holds. The resampler cannot be fed after this."""
        return self.feed(None)
