/**
 * Finnish formatting for dates, times and numbers. Input times are ISO 8601 UTC ("…Z");
 * output is Finnish time, whatever time zone the visitor is in.
 */

const LOCALE = "fi-FI";
const TIME_ZONE = "Europe/Helsinki";

type DateInput = string | number | Date | null | undefined;

function toDate(value: DateInput): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

const dateFormat = new Intl.DateTimeFormat(LOCALE, {
  day: "numeric",
  month: "numeric",
  year: "numeric",
  timeZone: TIME_ZONE,
});

const dayMonthFormat = new Intl.DateTimeFormat(LOCALE, {
  day: "numeric",
  month: "numeric",
  timeZone: TIME_ZONE,
});

const longDateFormat = new Intl.DateTimeFormat(LOCALE, {
  weekday: "long",
  day: "numeric",
  month: "long",
  year: "numeric",
  timeZone: TIME_ZONE,
});

const timeFormat = new Intl.DateTimeFormat(LOCALE, {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: TIME_ZONE,
});

const timeSecondsFormat = new Intl.DateTimeFormat(LOCALE, {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  timeZone: TIME_ZONE,
});

/** Sortable day key ("2026-11-12") in Finnish time, for same-day comparisons. */
const dayKeyFormat = new Intl.DateTimeFormat("en-CA", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  timeZone: TIME_ZONE,
});

/** "12.11.2026" */
export function formatDate(value: DateInput): string {
  const date = toDate(value);
  return date ? dateFormat.format(date) : "";
}

/** "torstai 12. marraskuuta 2026" */
export function formatLongDate(value: DateInput): string {
  const date = toDate(value);
  return date ? longDateFormat.format(date) : "";
}

/** "14.05" */
export function formatTime(value: DateInput): string {
  const date = toDate(value);
  return date ? timeFormat.format(date) : "";
}

/** "14.05.09" */
export function formatTimeWithSeconds(value: DateInput): string {
  const date = toDate(value);
  return date ? timeSecondsFormat.format(date) : "";
}

/** "12.11.2026 klo 14.05" */
export function formatDateTime(value: DateInput): string {
  const date = toDate(value);
  return date ? `${dateFormat.format(date)} klo ${timeFormat.format(date)}` : "";
}

/** Today "14.05", this year "12.11. klo 14.05", older "12.11.2025". For lists and logs. */
export function formatWhen(value: DateInput, now: Date = new Date()): string {
  const date = toDate(value);
  if (!date) return "";
  const day = dayKeyFormat.format(date);
  const today = dayKeyFormat.format(now);
  if (day === today) return timeFormat.format(date);
  if (day.slice(0, 4) === today.slice(0, 4)) {
    return `${dayMonthFormat.format(date)} klo ${timeFormat.format(date)}`;
  }
  return dateFormat.format(date);
}

const relativeFormat = new Intl.RelativeTimeFormat("fi", { numeric: "auto" });

/** "juuri nyt", "3 minuuttia sitten", "eilen", "2 päivää sitten" */
export function formatRelative(value: DateInput, now: Date = new Date()): string {
  const date = toDate(value);
  if (!date) return "";
  const seconds = Math.round((date.getTime() - now.getTime()) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 45) return "juuri nyt";
  if (abs < 3600) return relativeFormat.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return relativeFormat.format(Math.round(seconds / 3600), "hour");
  if (abs < 86400 * 30) return relativeFormat.format(Math.round(seconds / 86400), "day");
  return formatDate(date);
}

/** "1 234,5" */
export function formatNumber(value: number | null | undefined, maximumFractionDigits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "";
  return new Intl.NumberFormat(LOCALE, { maximumFractionDigits }).format(value);
}

/** A share 0..1 as "87 %" */
export function formatPercent(share: number | null | undefined, maximumFractionDigits = 0): string {
  if (share === null || share === undefined || !Number.isFinite(share)) return "";
  return new Intl.NumberFormat(LOCALE, { style: "percent", maximumFractionDigits }).format(share);
}

/** Milliseconds as "320 ms", "1,2 s" or "2 min 5 s" */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${formatNumber(seconds, seconds < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds - minutes * 60);
  return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
}

/** Bytes as "640 t", "12 kt" or "1,4 Mt" */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return "";
  if (bytes < 1000) return `${Math.round(bytes)} t`;
  if (bytes < 1_000_000) return `${formatNumber(bytes / 1000, 0)} kt`;
  return `${formatNumber(bytes / 1_000_000, 1)} Mt`;
}

/** "3/5" for filled and total counts. */
export function formatFraction(filled: number, total: number): string {
  return `${filled}/${total}`;
}
