import { ApiError } from "@/lib/api";
import type { Tier } from "@/lib/types";

/** The four tiers from the most to the least restricted: the order of the ladder. */
export const TIERS: readonly Tier[] = ["prohibited", "high", "transparency", "minimal"];

export const TIER_LABEL: Record<Tier, string> = {
  prohibited: "Prohibited practice",
  high: "High risk",
  transparency: "Transparency obligation",
  minimal: "Minimal risk",
};

export const WHO_LABEL: Record<string, string> = {
  provider: "Provider",
  deployer: "Deployer",
  both: "Provider and deployer",
};

const dateFormat = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "long",
  year: "numeric",
  timeZone: "UTC",
});

/** "2026-08-02" becomes "2 August 2026"; anything else is shown as the server wrote it. */
export function formatAppliesFrom(value: string | null | undefined): string {
  if (!value) return "";
  if (/^\d{4}-\d{2}-\d{2}/.test(value)) {
    const date = new Date(value.slice(0, 10) + "T00:00:00Z");
    if (!Number.isNaN(date.getTime())) return dateFormat.format(date);
  }
  return value;
}

/** "12 November 2026" for the evaluation's run time. */
export function formatRunDate(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : dateFormat.format(date);
}

/** Whole per cent of a probability between 0 and 1. */
export function percent(value: number | undefined): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return 0;
  return Math.round(Math.min(1, Math.max(0, value)) * 100);
}

/**
 * The sentence to show for a failed call. The server's own message is used when there is
 * one; a call that never got an answer gets an English sentence here.
 */
export function failureMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "stream_interrupted") {
      return "The connection dropped before the answer was complete. Try again.";
    }
    if (error.status === 0) return "The server could not be reached. Check the connection and try again.";
    if (error.status === 404) return "This was not found on the server.";
    if (error.status === 429) return "Too many requests. Wait a moment and try again.";
    return error.message;
  }
  return "The request failed. Try again.";
}
