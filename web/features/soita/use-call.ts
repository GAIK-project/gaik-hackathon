"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  CallClientMessage,
  CallLanguage,
  CallServerMessage,
  CallState,
  ReportType,
} from "@/lib/types";
import { WS_CLOSE_UNAUTHORIZED } from "@/lib/types";
import { wsUrl } from "@/lib/ws";

import type { CallPhase, Caption } from "./types";
import { useAudioPipeline } from "./use-audio";

export interface CallView {
  phase: CallPhase;
  state: CallState | null;
  /** The type the server confirmed in `ready`. */
  reportType: ReportType | null;
  reportId: string | null;
  values: Record<string, string>;
  filled: number;
  total: number;
  /** Name of the field the agent is asking for. */
  asking: string | null;
  caption: Caption | null;
  mic: boolean;
  /** A message to show: why the call did not start or ended. */
  notice: string | null;
}

export interface Call extends CallView {
  active: boolean;
  start: (options: { typeId: string; language: CallLanguage; mic: boolean; text?: string }) => void;
  sendText: (text: string) => void;
  stop: () => void;
  /** 0..1: the microphone while the agent listens, the agent's voice while it speaks. */
  level: () => number;
}

const IDLE: CallView = {
  phase: "idle",
  state: null,
  reportType: null,
  reportId: null,
  values: {},
  filled: 0,
  total: 0,
  asking: null,
  caption: null,
  mic: false,
  notice: null,
};

const STOP_GRACE_MS = 5000;

/** The call of ARCHITECTURE.md section 8.2 over one WebSocket. */
export function useCall(): Call {
  const audio = useAudioPipeline();
  const [view, setView] = useState<CallView>(IDLE);
  const wsRef = useRef<WebSocket | null>(null);
  const readyRef = useRef(false);
  const savedRef = useRef(false);
  const speakingRef = useRef(false);
  const queuedTextRef = useRef<string | null>(null);
  const stopTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const patch = useCallback((next: Partial<CallView>) => {
    setView((current) => ({ ...current, ...next }));
  }, []);

  const send = useCallback((message: CallClientMessage) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message));
  }, []);

  const teardown = useCallback(() => {
    clearTimeout(stopTimerRef.current);
    audio.stop();
    const ws = wsRef.current;
    wsRef.current = null;
    readyRef.current = false;
    speakingRef.current = false;
    queuedTextRef.current = null;
    if (ws) {
      ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) ws.close();
    }
  }, [audio]);

  const start = useCallback<Call["start"]>(
    ({ typeId, language, mic, text }) => {
      if (wsRef.current) return;
      savedRef.current = false;
      queuedTextRef.current = text?.trim() || null;
      setView({
        ...IDLE,
        phase: "connecting",
        caption: queuedTextRef.current ? { role: "caller", text: queuedTextRef.current } : null,
      });

      // Inside the press: the audio context may only start from a user gesture.
      const playback = audio.startPlayback().catch(() => {});

      let ws: WebSocket;
      try {
        ws = new WebSocket(wsUrl("/api/call/ws"));
      } catch {
        audio.stop();
        setView({ ...IDLE, notice: "The call could not be connected. Try again." });
        return;
      }
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () => {
        ws.send(JSON.stringify({ type: "start", type_id: typeId, language } satisfies CallClientMessage));
        if (!mic) return;
        void playback
          .then(() =>
            audio.startMic((pcm) => {
              if (readyRef.current && ws.readyState === WebSocket.OPEN) ws.send(pcm);
            }),
          )
          .then(() => {
            if (wsRef.current === ws) patch({ mic: true });
          })
          .catch(() => {
            if (wsRef.current === ws) {
              patch({ mic: false, notice: "No microphone available. Type your turns instead." });
            }
          });
      };

      ws.onmessage = (event: MessageEvent) => {
        if (event.data instanceof ArrayBuffer) {
          audio.enqueue(event.data);
          return;
        }
        if (typeof event.data !== "string") return;
        let message: CallServerMessage;
        try {
          message = JSON.parse(event.data) as CallServerMessage;
        } catch {
          return;
        }
        switch (message.type) {
          case "ready": {
            readyRef.current = true;
            const required = message.report_type.fields.filter((field) => field.required).length;
            patch({
              phase: "live",
              state: "listening",
              reportId: message.report_id,
              reportType: message.report_type,
              total: required,
            });
            const queued = queuedTextRef.current;
            queuedTextRef.current = null;
            if (queued) ws.send(JSON.stringify({ type: "text", text: queued } satisfies CallClientMessage));
            break;
          }
          case "state":
            speakingRef.current = message.value === "speaking";
            patch(
              message.value === "saving" || message.value === "done"
                ? { state: message.value, phase: "saving" }
                : { state: message.value },
            );
            break;
          case "transcript":
            if (message.text.trim()) patch({ caption: { role: message.role, text: message.text } });
            break;
          case "fields":
            patch({
              values: message.values,
              filled: message.filled,
              total: message.total,
              asking: message.asking,
            });
            break;
          case "interrupt":
            audio.flush();
            break;
          case "saved":
            savedRef.current = true;
            patch({ phase: "saved", state: "done", reportId: message.report_id, asking: null });
            break;
          case "error":
            patch({ notice: message.message });
            break;
        }
      };

      ws.onerror = () => {
        // onclose follows and reports it.
      };

      ws.onclose = (event: CloseEvent) => {
        const wasReady = readyRef.current;
        const saved = savedRef.current;
        teardown();
        setView((current) => {
          if (saved) return { ...current, phase: "saved", state: "done", mic: false };
          let notice = current.notice;
          if (!notice) {
            if (event.code === WS_CLOSE_UNAUTHORIZED) notice = "Your session has expired. Reload the page.";
            else if (!wasReady) notice = "The call could not be connected. Try again.";
          }
          // A call that ended with something recorded is finished by the server: keep the
          // fields on screen so the board row and this panel agree.
          const kept = wasReady && Object.keys(current.values).length > 0;
          return kept
            ? { ...current, phase: "saved", state: "done", mic: false, asking: null, notice }
            : { ...IDLE, notice };
        });
      };
    },
    [audio, patch, teardown],
  );

  const stop = useCallback(() => {
    const ws = wsRef.current;
    if (!ws) return;
    audio.flush();
    if (ws.readyState === WebSocket.OPEN && readyRef.current) {
      send({ type: "stop" });
      patch({ phase: "saving", state: "saving" });
      clearTimeout(stopTimerRef.current);
      stopTimerRef.current = setTimeout(() => ws.close(), STOP_GRACE_MS);
    } else {
      teardown();
      setView(IDLE);
    }
  }, [audio, patch, send, teardown]);

  const sendText = useCallback(
    (text: string) => {
      const clean = text.trim();
      if (!clean) return;
      if (readyRef.current) {
        audio.flush();
        send({ type: "text", text: clean });
        patch({ caption: { role: "caller", text: clean } });
      } else {
        queuedTextRef.current = clean;
      }
    },
    [audio, patch, send],
  );

  const level = useCallback(
    () => (speakingRef.current ? audio.outputLevel() : audio.inputLevel()),
    [audio],
  );

  useEffect(() => teardown, [teardown]);

  const active = view.phase === "connecting" || view.phase === "live" || view.phase === "saving";

  return useMemo(
    () => ({ ...view, active, start, sendText, stop, level }),
    [view, active, start, sendText, stop, level],
  );
}
