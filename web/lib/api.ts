import type { ApiErrorBody } from "@/lib/types";

/**
 * Typed fetch for `/api/**`.
 *
 * Production: the app is served by FastAPI, so calls go to the same origin.
 * Development: `next dev` on :3000 calls the API on :8787 (NEXT_PUBLIC_API_BASE, CORS with
 * HACK_DEV=1). Credentials are always included, so the session cookie travels either way.
 */

const DEV_API_BASE = "http://localhost:8787";

/** Origin of the API without a trailing slash; "" means same origin. */
export function apiBase(): string {
  const fromEnv = process.env.NEXT_PUBLIC_API_BASE;
  if (fromEnv) return fromEnv.replace(/\/+$/, "");
  return process.env.NODE_ENV === "development" ? DEV_API_BASE : "";
}

/** Absolute URL for an API path ("/api/reports") or a file link the API returned. */
export function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  return `${apiBase()}${path.startsWith("/") ? path : `/${path}`}`;
}

const FALLBACK_MESSAGES: Record<string, string> = {
  network: "Yhteys palvelimeen katkesi. Tarkista verkko ja yritä uudelleen.",
  unauthorized: "Kirjautuminen on vanhentunut. Lataa sivu uudelleen.",
  not_found: "Haettua kohdetta ei löytynyt.",
  too_large: "Tiedosto on liian suuri.",
  rate_limited: "Pyyntöjä tuli liian tiheään. Odota hetki ja yritä uudelleen.",
  server_error: "Palvelussa tapahtui virhe. Yritä hetken päästä uudelleen.",
  stream_interrupted: "Yhteys katkesi kesken vastauksen. Yritä uudelleen.",
  unknown: "Pyyntö epäonnistui. Yritä uudelleen.",
};

function fallbackCode(status: number): string {
  if (status === 401 || status === 403) return "unauthorized";
  if (status === 404) return "not_found";
  if (status === 413) return "too_large";
  if (status === 429) return "rate_limited";
  if (status >= 500) return "server_error";
  return "unknown";
}

/**
 * Every failed call throws this. `message` is a Finnish sentence that can be shown to the
 * user as it is: the server's own when the response carried the error envelope of section 4,
 * a generic one otherwise. Provider error text never reaches here.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message?: string) {
    super(message || FALLBACK_MESSAGES[code] || FALLBACK_MESSAGES.unknown);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }

  /** The request never got an HTTP answer. */
  get isNetwork(): boolean {
    return this.status === 0;
  }
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== "object" || value === null) return false;
  const error = (value as { error?: unknown }).error;
  if (typeof error !== "object" || error === null) return false;
  const { code, message } = error as { code?: unknown; message?: unknown };
  return typeof code === "string" && typeof message === "string";
}

/** Turns a non-2xx response into an ApiError, reading the envelope when there is one. */
export async function errorFromResponse(response: Response): Promise<ApiError> {
  try {
    const body: unknown = await response.clone().json();
    if (isErrorBody(body)) {
      return new ApiError(response.status, body.error.code, body.error.message);
    }
  } catch {
    // Not JSON: fall through to the generic message for this status.
  }
  return new ApiError(response.status, fallbackCode(response.status));
}

/** Turns anything thrown by fetch into an ApiError. Aborts are passed through untouched. */
export function toApiError(error: unknown): unknown {
  if (error instanceof ApiError) return error;
  if (error instanceof DOMException && error.name === "AbortError") return error;
  return new ApiError(0, "network");
}

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export interface ApiRequestInit extends Omit<RequestInit, "body"> {
  /** Sent as JSON with the right header. */
  json?: unknown;
  /** Sent as it is (FormData for uploads). */
  body?: BodyInit | null;
  /** Query string values; undefined and null are dropped. */
  query?: Record<string, string | number | boolean | undefined | null>;
}

function withQuery(path: string, query: ApiRequestInit["query"]): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null) params.set(key, String(value));
  }
  const text = params.toString();
  if (!text) return path;
  return `${path}${path.includes("?") ? "&" : "?"}${text}`;
}

/** The raw response of a successful call; throws ApiError otherwise. */
export async function apiFetch(path: string, init: ApiRequestInit = {}): Promise<Response> {
  const { json, query, headers, body, ...rest } = init;
  const finalHeaders = new Headers(headers);
  if (!finalHeaders.has("Accept")) finalHeaders.set("Accept", "application/json");
  let finalBody = body;
  if (json !== undefined) {
    finalHeaders.set("Content-Type", "application/json");
    finalBody = JSON.stringify(json);
  }

  let response: Response;
  try {
    response = await fetch(apiUrl(withQuery(path, query)), {
      ...rest,
      headers: finalHeaders,
      body: finalBody,
      credentials: "include",
    });
  } catch (error) {
    throw toApiError(error);
  }
  if (!response.ok) throw await errorFromResponse(response);
  return response;
}

async function request<T>(path: string, init: ApiRequestInit): Promise<T> {
  const response = await apiFetch(path, init);
  if (response.status === 204) return undefined as T;
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(response.status, "unknown");
  }
}

type Options = Omit<ApiRequestInit, "method" | "json" | "body">;

export const api = {
  get: <T>(path: string, options?: Options) => request<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, json?: unknown, options?: Options) =>
    request<T>(path, { ...options, method: "POST", json }),
  patch: <T>(path: string, json?: unknown, options?: Options) =>
    request<T>(path, { ...options, method: "PATCH", json }),
  delete: <T>(path: string, options?: Options) =>
    request<T>(path, { ...options, method: "DELETE" }),
  /** Multipart upload. Do not set Content-Type: the browser adds the boundary. */
  postForm: <T>(path: string, form: FormData, options?: Options) =>
    request<T>(path, { ...options, method: "POST", body: form }),
  /** A file the API answers with (docx, audio), as a Blob. */
  blob: async (path: string, init: ApiRequestInit = {}): Promise<Blob> => {
    const headers = new Headers(init.headers);
    if (!headers.has("Accept")) headers.set("Accept", "*/*");
    const response = await apiFetch(path, { ...init, headers });
    return response.blob();
  },
};

/** The Finnish sentence to show for anything a call threw. */
export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : FALLBACK_MESSAGES.unknown;
}
