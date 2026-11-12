// Mic capture AudioWorklet: Float32 (mono, 24 kHz) -> Int16 PCM frames + RMS.
//
// Runs in the audio rendering thread. Plain ES — NOT bundled, no imports.
// The AudioContext is created at 24 kHz, so `sampleRate` here is 24000 and one
// 512-sample frame is ~21 ms. Each frame is posted to the main thread, which
// forwards it as a binary WebSocket frame to the relay backend.

const FRAME_SAMPLES = 512; // 4 render quanta (128 each) ~= 21 ms at 24 kHz

class MicCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = new Float32Array(FRAME_SAMPLES);
    this._offset = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true; // no input yet — keep the processor alive

    let i = 0;
    while (i < channel.length) {
      const take = Math.min(FRAME_SAMPLES - this._offset, channel.length - i);
      this._buf.set(channel.subarray(i, i + take), this._offset);
      this._offset += take;
      i += take;
      if (this._offset >= FRAME_SAMPLES) {
        this._emit();
        this._offset = 0;
      }
    }
    return true;
  }

  _emit() {
    const pcm = new Int16Array(FRAME_SAMPLES);
    let sumSq = 0;
    for (let j = 0; j < FRAME_SAMPLES; j++) {
      let s = this._buf[j];
      if (s > 1) s = 1;
      else if (s < -1) s = -1;
      pcm[j] = s < 0 ? s * 0x8000 : s * 0x7fff;
      sumSq += s * s;
    }
    const rms = Math.sqrt(sumSq / FRAME_SAMPLES);
    // Scale RMS into a lively 0..1 range for the Orb's input volume.
    const volume = Math.min(1, rms * 8);
    this.port.postMessage({ pcm: pcm.buffer, volume }, [pcm.buffer]);
  }
}

registerProcessor("mic-capture-processor", MicCaptureProcessor);
