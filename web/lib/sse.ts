import { ApiError, apiUrl, errorFromResponse, isAbort } from "@/lib/api";

/**
 * Server-sent events (docs/ARCHITECTURE.md section 4):
 * `event: <name>\ndata: <json>\n\n`, a `: ping` comment every 15 s, a final `event: done`.
 *
 * - `subscribe` reads a GET stream that never ends by itself (a live board) and reconnects
 *   with backoff when it drops.
 * - `postStream` reads the answer of one POST (generate, run, assess) to its `done`.
 *
 * Both read the body with fetch and a ReadableStream and share one parser. The GET stream
 * does not use EventSource: it cannot show the HTTP status (a 401 would be retried for
 * ever) and needs a listener registered per event name; the wire format is the same.
 */

/** Event name to payload, for example `{ report: ReportSummary }`. */
export type StreamEvents = Record<string, unknown>;

export type EventHandlers<E extends StreamEvents> = {
  [K in keyof E]?: (data: E[K]) => void;
};

export interface StreamHandlers<E extends StreamEvents> {
  /** One handler per event name; events without a handler are ignored. */
  on: EventHandlers<E>;
  /** The stream is open (again, after a reconnect). */
  onOpen?: () => void;
  /** The server sent `event: done`. */
  onDone?: () => void;
}

export interface SubscribeHandlers<E extends StreamEvents> extends StreamHandlers<E> {
  /** The connection dropped; a retry follows after `retryInMs`. */
  onDisconnect?: (retryInMs: number) => void;
  /** The server refused the stream (4xx); no retry follows. */
  onError?: (error: ApiError) => void;
}

export interface Subscription {
  close: () => void;
}

interface ParsedEvent {
  event: string;
  data: string;
}

/**
 * Incremental SSE parser. Feed it decoded text in any chunking; it calls `emit` once per
 * complete event. Comment lines (": ping") are dropped, CRLF and LF both work, several
 * `data:` lines are joined with a newline.
 */
export function createSseParser(emit: (event: ParsedEvent) => void) {
  let buffer = "";
  let eventName = "";
  let dataLines: string[] = [];

  function dispatch() {
    if (dataLines.length > 0 || eventName) {
      emit({ event: eventName || "message", data: dataLines.join("\n") });
    }
    eventName = "";
    dataLines = [];
  }

  function line(text: string) {
    if (text === "") {
      dispatch();
      return;
    }
    if (text.startsWith(":")) return; // comment, the 15 s ping
    const colon = text.indexOf(":");
    const field = colon === -1 ? text : text.slice(0, colon);
    let value = colon === -1 ? "" : text.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") eventName = value;
    else if (field === "data") dataLines.push(value);
    // "id" and "retry" are not used by this API.
  }

  return {
    feed(chunk: string) {
      buffer += chunk;
      let index: number;
      while ((index = buffer.search(/\r\n|\n|\r/)) !== -1) {
        // A lone "\r" at the very end may be the first half of "\r\n": wait for more.
        if (buffer[index] === "\r" && index === buffer.length - 1) break;
        const text = buffer.slice(0, index);
        const width = buffer[index] === "\r" && buffer[index + 1] === "\n" ? 2 : 1;
        buffer = buffer.slice(index + width);
        line(text);
      }
    },
    /** The stream ended: flush an event that had no closing blank line. */
    end() {
      if (buffer) line(buffer.replace(/\r$/, ""));
      buffer = "";
      dispatch();
    },
  };
}

function parseData(data: string): unknown {
  if (data === "") return null;
  try {
    return JSON.parse(data);
  } catch {
    return data;
  }
}

/**
 * Reads one response body to its end. Returns true when `event: done` arrived.
 * `onActivity` fires for every chunk, pings included.
 */
async function readStream<E extends StreamEvents>(
  response: Response,
  handlers: StreamHandlers<E>,
  onActivity?: () => void,
): Promise<boolean> {
  const body = response.body;
  if (!body) return false;
  let done = false;
  const on = handlers.on as Record<string, ((value: unknown) => void) | undefined>;
  const parser = createSseParser(({ event, data }) => {
    on[event]?.(parseData(data));
    if (event === "done") {
      done = true;
      handlers.onDone?.();
    }
  });

  const reader = body.getReader();
  const decoder = new TextDecoder();
  try {
    while (true) {
      const { value, done: finished } = await reader.read();
      if (finished) break;
      onActivity?.();
      parser.feed(decoder.decode(value, { stream: true }));
    }
    parser.feed(decoder.decode());
    parser.end();
  } finally {
    reader.releaseLock();
  }
  return done;
}

const RETRY_BASE_MS = 1000;
const RETRY_MAX_MS = 15000;
/** The server pings every 15 s; three missed pings mean a dead connection. */
const SILENCE_LIMIT_MS = 45000;
/** A stream that lived this long was healthy: the next retry starts from the base delay. */
const HEALTHY_AFTER_MS = 10000;

/** 1 s, 2 s, 4 s, 8 s, 15 s, 15 s … with up to 25 % jitter. */
export function retryDelay(attempt: number): number {
  const base = Math.min(RETRY_MAX_MS, RETRY_BASE_MS * 2 ** attempt);
  return Math.round(base * (0.75 + Math.random() * 0.25));
}

/**
 * Subscribes to a GET stream and keeps it alive: when the connection drops, goes silent or
 * the server ends it, it reconnects with backoff until `close()` is called. A 4xx answer
 * (other than 408 and 429) stops it and is reported through `onError`.
 *
 * After a reconnect the server sends only new events, so refetch the list in `onOpen`
 * when a missed change would matter.
 */
export function subscribe<E extends StreamEvents = StreamEvents>(
  url: string,
  handlers: SubscribeHandlers<E>,
): Subscription {
  let closed = false;
  let attempt = 0;
  let controller: AbortController | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;
  let silenceTimer: ReturnType<typeof setTimeout> | undefined;

  function armSilenceTimer() {
    clearTimeout(silenceTimer);
    silenceTimer = setTimeout(() => controller?.abort(), SILENCE_LIMIT_MS);
  }

  function scheduleRetry() {
    if (closed) return;
    const delay = retryDelay(attempt);
    attempt += 1;
    handlers.onDisconnect?.(delay);
    retryTimer = setTimeout(connect, delay);
  }

  async function connect() {
    if (closed) return;
    retryTimer = undefined;
    controller = new AbortController();
    let openedAt = 0;
    try {
      const response = await fetch(apiUrl(url), {
        method: "GET",
        headers: { Accept: "text/event-stream" },
        credentials: "include",
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) {
        const retryable = response.status >= 500 || response.status === 408 || response.status === 429;
        if (!retryable) {
          closed = true;
          handlers.onError?.(await errorFromResponse(response));
          return;
        }
        throw new ApiError(response.status, "server_error");
      }
      openedAt = Date.now();
      handlers.onOpen?.();
      armSilenceTimer();
      await readStream(response, handlers, armSilenceTimer);
    } catch {
      // Network error, abort by the silence timer or a 5xx: retry below.
    } finally {
      clearTimeout(silenceTimer);
    }
    if (openedAt && Date.now() - openedAt > HEALTHY_AFTER_MS) attempt = 0;
    scheduleRetry();
  }

  function onOnline() {
    if (closed || !retryTimer) return;
    clearTimeout(retryTimer);
    retryTimer = undefined;
    attempt = 0;
    void connect();
  }

  if (typeof window !== "undefined") window.addEventListener("online", onOnline);
  void connect();

  return {
    close() {
      closed = true;
      clearTimeout(retryTimer);
      clearTimeout(silenceTimer);
      controller?.abort();
      if (typeof window !== "undefined") window.removeEventListener("online", onOnline);
    },
  };
}

export interface PostStreamInit extends Omit<RequestInit, "method" | "body"> {
  /** Sent as JSON. */
  json?: unknown;
  /** Sent as it is (FormData for uploads). */
  body?: BodyInit | null;
}

/**
 * Sends one POST and reads its event stream to the end. Resolves after `event: done`.
 * Rejects with an ApiError when the server refuses the request, the network fails or the
 * stream ends without `done`. Aborting through `init.signal` resolves quietly.
 */
export async function postStream<E extends StreamEvents = StreamEvents>(
  url: string,
  init: PostStreamInit,
  handlers: StreamHandlers<E>,
): Promise<void> {
  const { json, body, headers, ...rest } = init;
  const finalHeaders = new Headers(headers);
  finalHeaders.set("Accept", "text/event-stream");
  let finalBody = body;
  if (json !== undefined) {
    finalHeaders.set("Content-Type", "application/json");
    finalBody = JSON.stringify(json);
  }

  let finished: boolean;
  try {
    const response = await fetch(apiUrl(url), {
      ...rest,
      method: "POST",
      headers: finalHeaders,
      body: finalBody,
      credentials: "include",
      cache: "no-store",
    });
    if (!response.ok) throw await errorFromResponse(response);
    handlers.onOpen?.();
    finished = await readStream(response, handlers);
  } catch (error) {
    if (isAbort(error)) return;
    if (error instanceof ApiError) throw error;
    throw new ApiError(0, "network");
  }
  if (!finished) throw new ApiError(0, "stream_interrupted");
}
