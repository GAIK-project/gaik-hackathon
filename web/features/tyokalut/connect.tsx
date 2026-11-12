"use client";

import { useState } from "react";
import { MousePointer2, Plug } from "lucide-react";

import { StackLogo } from "@/components/shell/stack-logo";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { CopyButton } from "./copy-button";
import type { ClientInfo } from "./types";

/** Short tab labels for the clients the server lists; any other client keeps its title. */
const SHORT: Record<string, string> = {
  "claude-code": "Claude Code",
  "mcp-json": ".mcp.json",
  "claude-desktop": "Claude",
  codex: "Codex",
  cursor: "Cursor",
  grok: "Grok Bot",
};

/** The real logo of the client where `public/stack` has one, a neutral glyph otherwise. */
function ClientMark({ id }: { id: string }) {
  const key = id.toLowerCase();
  if (key.includes("codex") || key.includes("openai")) return <StackLogo id="openai" size={16} />;
  if (key.includes("grok")) return <StackLogo id="grok" size={16} />;
  if (key.includes("claude")) return <StackLogo id="claude" size={16} />;
  if (key.includes("mcp")) return <StackLogo id="mcp" size={16} />;
  if (key.includes("cursor")) return <MousePointer2 aria-hidden className="size-4" />;
  return <Plug aria-hidden className="size-4" />;
}

/**
 * "Connect an agent": one tab per ready-made client, each with the paste-ready snippet the
 * API built for this server's address and the server's line on where the key goes. The
 * snippets carry a placeholder or an environment variable name, never the key.
 */
export function Connect({ clients }: { clients: ClientInfo[] }) {
  const [active, setActive] = useState(clients[0]?.id ?? "");
  if (clients.length === 0) return null;

  return (
    <section aria-labelledby="connect-heading">
      <h2 id="connect-heading" className="text-lg font-medium">
        Connect an agent
      </h2>
      <Tabs value={active} onValueChange={setActive} className="mt-3 gap-3">
        <TabsList className="h-auto w-full flex-wrap justify-start gap-1 bg-transparent p-0 group-data-horizontal/tabs:h-auto">
          {clients.map((client) => (
            <TabsTrigger
              key={client.id}
              value={client.id}
              title={client.title}
              className="press h-11 flex-none gap-2 rounded-lg px-3 text-[0.9375rem] data-active:bg-surface-2 dark:data-active:border-transparent dark:data-active:bg-surface-2"
            >
              <ClientMark id={client.id} />
              {SHORT[client.id] ?? client.title}
            </TabsTrigger>
          ))}
        </TabsList>
        {clients.map((client) => (
          <TabsContent key={client.id} value={client.id} className="animate-rise">
            <div className="flex items-start rounded-xl bg-surface">
              <pre
                tabIndex={0}
                className="no-scrollbar min-w-0 flex-1 overflow-x-auto rounded-xl p-4 pr-1 font-mono text-[0.8125rem] leading-relaxed text-fg/90 outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                {client.snippet.trim()}
              </pre>
              <CopyButton
                text={client.snippet.trim()}
                label={`Copy the ${SHORT[client.id] ?? client.title} snippet`}
                className="m-1 shrink-0"
              />
            </div>
            <p className="mt-2.5 text-sm text-muted-foreground">
              {client.note ??
                "Put the MCP key where the snippet has its placeholder. Ask the demo host for the key."}
            </p>
          </TabsContent>
        ))}
      </Tabs>
    </section>
  );
}
