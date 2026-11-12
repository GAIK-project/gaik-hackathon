import type { StatusTone } from "@/components/shell";
import type { CallLanguage, CallState, Channel } from "@/lib/types";

import type { BoardStatus, CallPhase } from "./types";

/** English interface text for the Soita page. Ids on the wire stay as the server sends them. */

export const PRIVACY_NOTICE = "Do not share personal data.";
export const WHATSAPP_NUMBER = "+1 555 150 8387";

export const LANGUAGES: { id: CallLanguage; label: string; name: string }[] = [
  { id: "en", label: "EN", name: "English" },
  { id: "fi", label: "FI", name: "Finnish" },
  { id: "sv", label: "SV", name: "Swedish" },
];

const CALL_WORDS: Record<CallState, string> = {
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
  saving: "Saving",
  done: "Saved",
};

export function callWord(phase: CallPhase, state: CallState | null): string {
  if (phase === "idle") return "Ready";
  if (phase === "connecting") return "Connecting";
  if (phase === "saving") return "Saving";
  if (phase === "saved") return "Saved";
  return state ? CALL_WORDS[state] : "Listening";
}

export const STATUS_WORDS: Record<BoardStatus, { word: string; tone: StatusTone }> = {
  live: { word: "Live", tone: "accent" },
  processing: { word: "Processing", tone: "accent" },
  needs_approval: { word: "Pending", tone: "warn" },
  sending: { word: "Sending", tone: "accent" },
  sent: { word: "Sent", tone: "ok" },
  failed: { word: "Failed", tone: "danger" },
};

export function statusWord(status: string): { word: string; tone: StatusTone } {
  return STATUS_WORDS[status as BoardStatus] ?? { word: status, tone: "muted" };
}

export const CHANNEL_NAMES: Record<Channel, string> = {
  call: "Call",
  whatsapp: "WhatsApp",
  upload: "Audio file",
  text: "Text",
  mcp: "Agent tool",
};

const URGENCY: Record<string, { word: string; tone: StatusTone }> = {
  kiireellinen: { word: "Urgent", tone: "danger" },
  urgent: { word: "Urgent", tone: "danger" },
  normaali: { word: "Normal", tone: "muted" },
  normal: { word: "Normal", tone: "muted" },
  "ei kiireellinen": { word: "Not urgent", tone: "muted" },
  "not urgent": { word: "Not urgent", tone: "muted" },
};

export function urgencyWord(label: string): { word: string; tone: StatusTone } {
  return URGENCY[label.toLowerCase()] ?? { word: label, tone: "muted" };
}

const HELSINKI = "Europe/Helsinki";
const timeFormat = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: HELSINKI,
});
const dayFormat = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  timeZone: HELSINKI,
});
const dayKey = new Intl.DateTimeFormat("en-CA", { timeZone: HELSINKI });

/** "14:05" today, "12 Nov 14:05" otherwise (Finnish time). */
export function when(value: string | null | undefined, now: Date = new Date()): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const time = timeFormat.format(date);
  return dayKey.format(date) === dayKey.format(now) ? time : `${dayFormat.format(date)} ${time}`;
}

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`;
}

export function fileSize(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return "";
  if (bytes < 1000) return `${Math.round(bytes)} B`;
  if (bytes < 1_000_000) return `${Math.round(bytes / 1000)} kB`;
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}
