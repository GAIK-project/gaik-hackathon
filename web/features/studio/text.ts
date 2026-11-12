import { ApiError } from "@/lib/api";
import type { StudioSpec } from "@/lib/types";

import type { StreamError } from "./types";

export const EXAMPLES = [
  {
    label: "Invoices into a table",
    prompt:
      "Read supplier invoices and collect the supplier, invoice number, due date and total into a table.",
  },
  {
    label: "Site diary from audio",
    prompt: "Turn a site manager's spoken daily diary into a construction site diary report.",
  },
  {
    label: "Ask the guidelines",
    prompt: "Answer questions from our guidelines and show the page each answer comes from.",
  },
] as const;

export const NOTICE_PERSONAL = "Do not share personal data.";
export const NOTICE_US = "The description is also processed in the United States.";

/** The notices the server put on the spec, or the fixed ones: the labels template sends text to a service in the United States. */
export function noticesFor(spec: StudioSpec & { notices?: string[] }): string[] {
  if (Array.isArray(spec.notices) && spec.notices.length > 0) return spec.notices;
  return spec.template === "documents_to_labels" ? [NOTICE_PERSONAL, NOTICE_US] : [NOTICE_PERSONAL];
}

const GENERIC: Record<string, string> = {
  network: "The connection to the server dropped. Check the network and try again.",
  unauthorized: "The session has expired. Reload the page.",
  not_found: "Studio is not available on this server yet.",
  too_large: "The file is too large.",
  rate_limited: "Too many requests. Wait a moment and try again.",
  server_error: "The service failed. Try again in a moment.",
  stream_interrupted: "The connection dropped before the answer was complete. Try again.",
  unknown: "The request failed. Try again.",
};

/**
 * The sentence to show for anything a call threw: the server's own message when it sent
 * one, an English sentence for the status otherwise (the shared fallbacks are not English).
 */
export function errorText(error: unknown): string {
  if (!(error instanceof ApiError)) return GENERIC.unknown;
  const shared = new ApiError(error.status, error.code).message;
  if (error.message && error.message !== shared) return error.message;
  return GENERIC[error.code] ?? GENERIC.unknown;
}

export function streamErrorText(data: StreamError | null | undefined, fallback: string): string {
  return data?.message?.trim() || fallback;
}

/** Milliseconds as "320 ms", "1.2 s" or "2 min 5 s". */
export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds - minutes * 60);
  return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
}

/** Bytes as "640 B", "12 kB" or "1.4 MB". */
export function bytes(size: number | null | undefined): string {
  if (size === null || size === undefined || !Number.isFinite(size)) return "";
  if (size < 1000) return `${Math.round(size)} B`;
  if (size < 1_000_000) return `${Math.round(size / 1000)} kB`;
  return `${(size / 1_000_000).toFixed(1)} MB`;
}

export function percent(share: number): string {
  return `${Math.round(Math.min(1, Math.max(0, share)) * 100)}%`;
}

/** Upload limits of section 15.3, checked before the request is sent. */
const MAX_DOCUMENT = 15_000_000;
const MAX_AUDIO = 25_000_000;
const MAX_DOCUMENTS = 10;

export function checkFiles(spec: StudioSpec, files: File[]): string | null {
  const audio = spec.input.kind === "audio";
  if (!audio && files.length > MAX_DOCUMENTS) return "At most 10 documents per run.";
  const limit = audio ? MAX_AUDIO : MAX_DOCUMENT;
  const tooLarge = files.find((file) => file.size > limit);
  if (tooLarge) {
    return `${tooLarge.name} is larger than ${audio ? "25" : "15"} MB.`;
  }
  return null;
}
