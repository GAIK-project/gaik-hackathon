// Playback AudioWorklet: buffers incoming PCM16 (mono, 24 kHz) and plays it
// back gap-free through a ring buffer.
//
// Runs in the audio rendering thread. Plain ES — NOT bundled, no imports.
// Main thread posts either a raw PCM16 ArrayBuffer (agent audio) or a
// {type:"flush"} control message (barge-in: drop everything still queued).

const RING_CAPACITY = 24000 * 12; // ~12 s of 24 kHz mono audio
const VOLUME_EVERY = 8; // post output volume every 8 render quanta (~43 ms)

class AudioPlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._ring = new Float32Array(RING_CAPACITY);
    this._read = 0;
    this._write = 0;
    this._available = 0;
    this._tick = 0;
    this._rmsAccum = 0;
    this._rmsCount = 0;

    this.port.onmessage = (event) => {
      const data = event.data;
      if (data && data.type === "flush") {
        this._read = this._write;
        this._available = 0;
        return;
      }
      if (data instanceof ArrayBuffer) {
        this._enqueue(new Int16Array(data));
      }
    };
  }

  _enqueue(int16) {
    for (let i = 0; i < int16.length; i++) {
      this._ring[this._write] = int16[i] / 0x8000;
      this._write = (this._write + 1) % RING_CAPACITY;
      if (this._available < RING_CAPACITY) {
        this._available++;
      } else {
        // Overflow (slow consumer) — drop the oldest sample.
        this._read = (this._read + 1) % RING_CAPACITY;
      }
    }
  }

  process(_inputs, outputs) {
    const out = outputs[0] && outputs[0][0];
    if (!out) return true;

    let sumSq = 0;
    for (let i = 0; i < out.length; i++) {
      let s = 0;
      if (this._available > 0) {
        s = this._ring[this._read];
        this._read = (this._read + 1) % RING_CAPACITY;
        this._available--;
      }
      out[i] = s; // underrun -> silence (0), no clicks
      sumSq += s * s;
    }

    this._rmsAccum += sumSq;
    this._rmsCount += out.length;
    if (++this._tick >= VOLUME_EVERY) {
      const rms = Math.sqrt(this._rmsAccum / this._rmsCount);
      this.port.postMessage({ volume: Math.min(1, rms * 5) });
      this._tick = 0;
      this._rmsAccum = 0;
      this._rmsCount = 0;
    }
    return true;
  }
}

registerProcessor("audio-playback-processor", AudioPlaybackProcessor);
