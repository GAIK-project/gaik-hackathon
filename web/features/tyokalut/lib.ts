import { apiBase } from "@/lib/api";

import type { ToolArg, ToolInfo } from "./types";

// ── Arguments ────────────────────────────────────────────────────────────────

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function schemaType(schema: Record<string, unknown>): string {
  if (typeof schema.type === "string") return schema.type;
  if (Array.isArray(schema.type)) return schema.type.filter((t) => t !== "null").join(" or ");
  const anyOf = schema.anyOf ?? schema.oneOf;
  if (Array.isArray(anyOf)) {
    return anyOf
      .map((item) => (isRecord(item) ? text(item.type) : ""))
      .filter((t) => t && t !== "null")
      .join(" or ");
  }
  return "";
}

function arg(name: string, detail: unknown, required: boolean): ToolArg {
  const optional = name.endsWith("?");
  const clean = optional ? name.slice(0, -1) : name;
  if (isRecord(detail)) {
    return {
      name: clean,
      type: schemaType(detail),
      required: typeof detail.required === "boolean" ? detail.required : required && !optional,
      description: text(detail.description),
    };
  }
  return { name: clean, type: "", required: required && !optional, description: text(detail) };
}

/**
 * The contract leaves the shape of `arguments` to the server. This reads a JSON schema
 * (`properties`, `required`), a list of names or objects, or a plain name-to-description map.
 */
export function toolArgs(value: unknown): ToolArg[] {
  if (Array.isArray(value)) {
    return value.flatMap((item) => {
      if (typeof item === "string") return [arg(item, "", true)];
      if (isRecord(item) && typeof item.name === "string") return [arg(item.name, item, true)];
      return [];
    });
  }
  if (!isRecord(value)) return [];
  if (isRecord(value.properties)) {
    const required = Array.isArray(value.required) ? value.required : [];
    return Object.entries(value.properties).map(([name, detail]) =>
      arg(name, detail, required.includes(name)),
    );
  }
  return Object.entries(value).map(([name, detail]) => arg(name, detail, true));
}

// ── What a row shows ─────────────────────────────────────────────────────────

/** The first sentence of the server's description: the one line under the tool's name. */
export function firstSentence(description: string): string {
  const match = description.match(/^[\s\S]*?[.!?](?=\s|$)/);
  return (match ? match[0] : description).trim();
}

/** The built-in sample the server runs for "Try", as shown on the page. */
export function sampleText(tool: ToolInfo): string | null {
  if (!tool.sample) return null;
  return Object.keys(tool.sample).length === 0 ? null : JSON.stringify(tool.sample, null, 2);
}

// ── Results ──────────────────────────────────────────────────────────────────

function parseJsonText(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return value;
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

/** The answer of POST /api/mcp/try: the tool's result, with or without an envelope. */
export function unwrapResult(response: unknown): { body: unknown; ms: number | null } {
  if (isRecord(response) && "result" in response) {
    return {
      body: parseJsonText(response.result),
      ms: typeof response.ms === "number" ? response.ms : null,
    };
  }
  return { body: parseJsonText(response), ms: null };
}

const RESULT_LIMIT = 1200;

/** The result as text, cut to a length that fits under a row. `hidden` counts what was cut. */
export function trimResult(body: unknown): { text: string; hidden: number } {
  const full =
    typeof body === "string" ? body : (JSON.stringify(body, null, 2) ?? String(body));
  if (full.length <= RESULT_LIMIT) return { text: full, hidden: 0 };
  return { text: `${full.slice(0, RESULT_LIMIT).trimEnd()} …`, hidden: full.length - RESULT_LIMIT };
}

// ── Formatting (English; Finnish time) ───────────────────────────────────────

const clock = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  timeZone: "Europe/Helsinki",
});

/** "14:05:09" */
export function clockTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : clock.format(date);
}

/** "320 ms", "1.2 s", "2 min 5 s" */
export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds - minutes * 60);
  return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
}

/** The MCP address as an agent needs it: absolute, also when the API answers with a path. */
export function absoluteUrl(url: string): string {
  if (/^https?:\/\//i.test(url)) return url;
  const base = apiBase() || (typeof window === "undefined" ? "" : window.location.origin);
  return `${base}${url.startsWith("/") ? url : `/${url}`}`;
}
