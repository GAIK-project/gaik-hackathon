import type { How } from "@/lib/types";

/**
 * "How this is made" for Tools for agents: the MCP server of `server/app/mcp/`
 * (ARCHITECTURE.md sections 8.5 and 15.10). Logo ids come from `lib/stack.ts`.
 */
export const how: How = {
  title: "The same components as an agent's tools",
  steps: [
    {
      id: "agent",
      title: "An agent connects",
      detail: "Claude Code, Claude, Codex or Cursor gets the address and a bearer key.",
      logo: "claude",
    },
    {
      id: "mcp",
      title: "MCP over HTTP",
      detail:
        "The FastAPI app serves /mcp as stateless Streamable HTTP. A request without the MCP key is refused.",
      logo: "mcp",
    },
    {
      id: "tool",
      title: "A tool runs a gaik component",
      detail:
        "Nine tools: parse, extract, transcribe, classify, search and apply the AI Act, and read or create reports.",
      gaik: "PyMuPDFParser, DataExtractor, Transcriber, JevClassifier",
      logo: "python",
    },
    {
      id: "limits",
      title: "Nothing leaves without a person",
      detail:
        "No tool sends mail, approves a report, fetches a URL or reads a path. A file arrives as base64 and is deleted after the call.",
      logo: "fastapi",
    },
    {
      id: "log",
      title: "The call shows on this page",
      detail:
        "Each call is published when it starts and when it ends: tool, client, duration and counts, never the arguments.",
      logo: "fastapi",
    },
  ],
  stack: ["mcp", "claude", "openai", "fastapi", "python", "azure", "typesafe"],
};
