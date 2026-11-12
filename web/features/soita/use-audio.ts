"use client";

import { useCallback, useMemo, useRef } from "react";

/**
 * Browser audio for the call, ported from GAIK's openai-voice-agent-with-structured-outputs.
 *
 * - Playback: PCM16 frames (mono, 24 kHz) from the server go to the playback worklet, a
 *   gap-free ring buffer that also reports the output level.
 * - Capture: getUserMedia to the capture worklet, which posts Int16 frames of about 21 ms
 *   and the input level.
 *
 * One AudioContext is created at 24 kHz inside the press that starts the call, so the
 * worklets see 24 kHz audio and nothing is resampled. Playback starts without the
 * microphone, so a typed call still speaks.
 */
export interface AudioPipeline {
  /** Creates the context and playback. Call from a user gesture. */
  startPlayback: () => Promise<void>;
  /** Adds the microphone; rejects when it is missing or refused. */
  startMic: (onChunk: (pcm: ArrayBuffer) => void) => Promise<void>;
  stop: () => void;
  enqueue: (pcm: ArrayBuffer) => void;
  /** Drops queued agent audio (the caller spoke over the agent). */
  flush: () => void;
  inputLevel: () => number;
  outputLevel: () => number;
}

const WORKLETS = "/worklets";

export function useAudioPipeline(): AudioPipeline {
  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const micRef = useRef<AudioWorkletNode | null>(null);
  const playbackRef = useRef<AudioWorkletNode | null>(null);
  const inputRef = useRef(0);
  const outputRef = useRef(0);

  const startPlayback = useCallback(async () => {
    if (ctxRef.current) return;
    const ctx = new AudioContext({ sampleRate: 24000 });
    ctxRef.current = ctx;
    await ctx.audioWorklet.addModule(`${WORKLETS}/mic-capture-processor.js`);
    await ctx.audioWorklet.addModule(`${WORKLETS}/audio-playback-processor.js`);
    if (ctxRef.current !== ctx) return; // stopped while loading

    const playback = new AudioWorkletNode(ctx, "audio-playback-processor", {
      numberOfInputs: 0,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    });
    playback.port.onmessage = (event: MessageEvent) => {
      const volume = (event.data as { volume?: unknown } | null)?.volume;
      if (typeof volume === "number") outputRef.current = volume;
    };
    playback.connect(ctx.destination);
    playbackRef.current = playback;
    if (ctx.state !== "running") await ctx.resume().catch(() => {});
  }, []);

  const startMic = useCallback(async (onChunk: (pcm: ArrayBuffer) => void) => {
    const ctx = ctxRef.current;
    if (!ctx) throw new Error("no audio context");
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("no microphone");
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    if (ctxRef.current !== ctx) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    streamRef.current = stream;

    const source = ctx.createMediaStreamSource(stream);
    const mic = new AudioWorkletNode(ctx, "mic-capture-processor", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    });
    mic.port.onmessage = (event: MessageEvent) => {
      const { pcm, volume } = (event.data ?? {}) as { pcm?: unknown; volume?: unknown };
      if (typeof volume === "number") inputRef.current = volume;
      if (pcm instanceof ArrayBuffer) onChunk(pcm);
    };
    micRef.current = mic;

    // Kept in the graph through a silent gain so process() is pulled; nothing is heard.
    const silent = ctx.createGain();
    silent.gain.value = 0;
    source.connect(mic);
    mic.connect(silent);
    silent.connect(ctx.destination);
  }, []);

  const stop = useCallback(() => {
    try {
      micRef.current?.disconnect();
    } catch {
      /* already detached */
    }
    try {
      playbackRef.current?.disconnect();
    } catch {
      /* already detached */
    }
    streamRef.current?.getTracks().forEach((track) => track.stop());
    const ctx = ctxRef.current;
    if (ctx && ctx.state !== "closed") ctx.close().catch(() => {});
    micRef.current = null;
    playbackRef.current = null;
    streamRef.current = null;
    ctxRef.current = null;
    inputRef.current = 0;
    outputRef.current = 0;
  }, []);

  const enqueue = useCallback((pcm: ArrayBuffer) => {
    playbackRef.current?.port.postMessage(pcm, [pcm]);
  }, []);

  const flush = useCallback(() => {
    playbackRef.current?.port.postMessage({ type: "flush" });
    outputRef.current = 0;
  }, []);

  const inputLevel = useCallback(() => inputRef.current, []);
  const outputLevel = useCallback(() => outputRef.current, []);

  return useMemo(
    () => ({ startPlayback, startMic, stop, enqueue, flush, inputLevel, outputLevel }),
    [startPlayback, startMic, stop, enqueue, flush, inputLevel, outputLevel],
  );
}
