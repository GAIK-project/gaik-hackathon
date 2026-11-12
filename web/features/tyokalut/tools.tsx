"use client";

import { useState } from "react";
import { ChevronRight, LoaderCircle, Play } from "lucide-react";
import { cn } from "cn";

import { Pressable } from "@/components/shell/pressable";
import { StatusWord } from "@/components/shell/status-word";
import { Button } from "@/components/ui/button";
import { api, errorMessage } from "@/lib/api";

import { duration, firstSentence, sampleText, toolArgs, trimResult, unwrapResult } from "./lib";
import type { ToolInfo, TryState } from "./types";

/** One run of a tool on the server, with its built-in sample (no arguments are sent). */
async function tryTool(name: string): Promise<TryState> {
  try {
    const { body, ms } = unwrapResult(await api.post<unknown>("/api/mcp/try", { tool: name }));
    return { status: "done", ...trimResult(body), ms };
  } catch (error) {
    return { status: "error", message: errorMessage(error) };
  }
}

/**
 * The server's tools as a quiet list. A press on a row opens what the tool takes; "Try"
 * runs it on the server (POST /api/mcp/try) with the server's small built-in sample and
 * shows the trimmed result under the row. The call also lands in the activity log.
 */
export function Tools({ tools, notices }: { tools: ToolInfo[]; notices: string[] }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [tries, setTries] = useState<Record<string, TryState>>({});

  async function run(name: string) {
    setTries((current) => ({ ...current, [name]: { status: "running" } }));
    const state = await tryTool(name);
    setTries((current) => ({ ...current, [name]: state }));
  }

  return (
    <section aria-labelledby="tools-heading">
      <h2 id="tools-heading" className="text-lg font-medium">
        Tools
      </h2>
      {notices.length > 0 && (
        <p className="mt-1 text-sm text-muted-foreground">{notices.join(" ")}</p>
      )}
      <ul className="mt-2">
        {tools.map((tool) => (
          <ToolRow
            key={tool.name}
            tool={tool}
            notices={notices}
            open={Boolean(open[tool.name])}
            state={tries[tool.name]}
            onToggle={() => setOpen((current) => ({ ...current, [tool.name]: !current[tool.name] }))}
            onTry={() => void run(tool.name)}
          />
        ))}
      </ul>
    </section>
  );
}

function ToolRow({
  tool,
  notices,
  open,
  state,
  onToggle,
  onTry,
}: {
  tool: ToolInfo;
  notices: string[];
  open: boolean;
  state: TryState | undefined;
  onToggle: () => void;
  onTry: () => void;
}) {
  const running = state?.status === "running";
  const panelId = `tool-${tool.name}-detail`;

  return (
    <li className="border-b border-line/70 last:border-b-0">
      <div className="flex items-center gap-2">
        <Pressable
          onClick={onToggle}
          aria-expanded={open}
          aria-controls={panelId}
          className="flex min-h-16 min-w-0 flex-1 items-center gap-2.5 rounded-lg py-3 text-left"
        >
          <ChevronRight
            aria-hidden
            className={cn(
              "size-4 shrink-0 text-muted-foreground transition-transform duration-200 ease-stage",
              open && "rotate-90",
            )}
          />
          <span className="min-w-0">
            <span className="block truncate font-mono text-[0.9375rem]">{tool.name}</span>
            <span
              className={cn("mt-0.5 text-sm text-muted-foreground", open ? "block" : "line-clamp-2")}
            >
              {firstSentence(tool.description)}
            </span>
          </span>
        </Pressable>
        <Button
          type="button"
          variant="secondary"
          onClick={onTry}
          disabled={running}
          aria-label={`Try ${tool.name}`}
          className="min-w-[6.5rem] disabled:opacity-100"
        >
          {running ? (
            <LoaderCircle className="animate-spin text-accent" aria-hidden />
          ) : (
            <Play aria-hidden />
          )}
          <StatusWord>{running ? "Running" : "Try"}</StatusWord>
        </Button>
      </div>

      {open && <Detail id={panelId} tool={tool} notices={notices} />}
      {state && state.status !== "running" && <Result state={state} />}
    </li>
  );
}

const BLOCK =
  "no-scrollbar overflow-auto rounded-lg bg-surface p-3 font-mono text-[0.8125rem] leading-relaxed break-words whitespace-pre-wrap text-fg/85 outline-none focus-visible:ring-2 focus-visible:ring-accent";

function Detail({ id, tool, notices }: { id: string; tool: ToolInfo; notices: string[] }) {
  const args = toolArgs(tool.arguments);
  const sample = sampleText(tool);
  // The rest of the server's description; the fixed notices stand once, above the list.
  const rest = notices
    .reduce((text, notice) => text.replace(notice, ""), tool.description)
    .slice(firstSentence(tool.description).length)
    .trim();
  return (
    <div id={id} className="animate-rise pb-4 pl-[1.625rem]">
      {rest && <p className="mb-3 max-w-[68ch] text-sm text-muted-foreground">{rest}</p>}
      {args.length === 0 ? (
        <p className="text-sm text-muted-foreground">This tool takes no arguments.</p>
      ) : (
        <dl className="grid gap-1.5">
          {args.map((a) => (
            <div key={a.name} className="flex flex-wrap items-baseline gap-x-3">
              <dt className="font-mono text-sm">{a.name}</dt>
              <dd className="text-sm text-muted-foreground">
                {[a.type, a.required ? "" : "optional"].filter(Boolean).join(", ")}
                {a.description && `: ${a.description}`}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {sample && (
        <>
          <p className="mt-3 text-sm text-muted-foreground">Try runs this built-in sample:</p>
          <pre tabIndex={0} className={cn(BLOCK, "mt-1.5 max-h-56")}>
            {sample}
          </pre>
        </>
      )}
    </div>
  );
}

function Result({ state }: { state: Exclude<TryState, { status: "running" }> }) {
  if (state.status === "error") {
    return (
      <p role="alert" className="animate-rise pb-4 pl-[1.625rem] text-sm text-danger">
        {state.message}
      </p>
    );
  }
  return (
    <div className="animate-rise pb-4 pl-[1.625rem]">
      <p className="text-sm text-muted-foreground">
        <span className="text-ok">Done</span>
        {state.ms !== null && <span className="tabular-nums"> in {duration(state.ms)}</span>}
      </p>
      <pre tabIndex={0} className={cn(BLOCK, "mt-1.5 max-h-72")}>
        {state.text}
      </pre>
      {state.hidden > 0 && (
        <p className="mt-1.5 text-xs text-muted-foreground">
          Trimmed for the page: an agent gets {state.hidden.toLocaleString("en-GB")} more
          characters.
        </p>
      )}
    </div>
  );
}
