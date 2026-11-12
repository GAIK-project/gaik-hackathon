import type { McpClient, McpTool } from "@/lib/types";

/** One argument of a tool, whatever shape the server described it in. */
export interface ToolArg {
  name: string;
  type: string;
  required: boolean;
  description: string;
}

/**
 * A tool of GET /api/mcp/info as the server sends it (`server/app/mcp/server.py`):
 * `arguments` is the tool's JSON schema, `sample` the built-in arguments "Try" runs with
 * (file content replaced by a placeholder).
 */
export interface ToolInfo extends McpTool {
  model_backed?: boolean;
  sample?: Record<string, unknown>;
}

/** A client of GET /api/mcp/info: `note` is the server's line on where the key goes. */
export interface ClientInfo extends McpClient {
  language?: string;
  note?: string;
}

export interface Info {
  url: string;
  /** Fixed notices, such as "Do not share personal data." */
  notices: string[];
  tools: ToolInfo[];
  clients: ClientInfo[];
}

/** The answer of POST /api/mcp/try. A failure is the usual error envelope. */
export interface TryAnswer {
  tool: string;
  ok: boolean;
  ms: number;
  arguments: Record<string, unknown>;
  result: unknown;
}

/** A row of the activity log; the server adds `status`, and a running call has no `ok` or `ms`. */
export interface ActivityRow {
  id: string;
  at: string;
  tool: string;
  client: string | null;
  ok: boolean | null;
  ms: number | null;
  summary: string;
  status?: "running" | "done" | "error";
}

/** GET /api/mcp/activity/stream */
export type ActivityEvents = { call: ActivityRow };

/** What one press of "Try" left behind. */
export type TryState =
  | { status: "running" }
  | { status: "done"; text: string; hidden: number; ms: number | null }
  | { status: "error"; message: string };
