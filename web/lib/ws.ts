import { apiBase } from "@/lib/api";

/**
 * WebSocket URL for an API path, for example `wsUrl("/api/call/ws")`.
 * Derived from NEXT_PUBLIC_API_BASE when it is set (http becomes ws, https becomes wss),
 * otherwise from the page's own origin. The handshake authenticates with the session
 * cookie; the server refuses with close code 4401 (WS_CLOSE_UNAUTHORIZED in lib/types).
 */
export function wsUrl(path: string): string {
  const suffix = path.startsWith("/") ? path : `/${path}`;
  const base = apiBase();
  if (base) return `${base.replace(/^http/i, "ws")}${suffix}`;
  if (typeof window === "undefined") return suffix;
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}${suffix}`;
}
