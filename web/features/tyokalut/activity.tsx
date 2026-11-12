"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { cn } from "cn";

import { StatusWord } from "@/components/shell/status-word";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, errorMessage } from "@/lib/api";
import { subscribe } from "@/lib/sse";
import { clockTime, duration } from "./lib";
import type { ActivityEvents, ActivityRow } from "./types";

const KEEP = 60;

function isRow(value: unknown): value is ActivityRow {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as ActivityRow).id === "string" &&
    typeof (value as ActivityRow).tool === "string"
  );
}

/** Still running: the "started" event of a call has no duration yet. */
function isRunning(row: ActivityRow): boolean {
  return row.status ? row.status === "running" : row.ms === null || row.ms === undefined;
}

/** Upserts by id, newest first. A finished row is never replaced by its own "started". */
function merge(current: ActivityRow[], incoming: ActivityRow[]): ActivityRow[] {
  const byId = new Map(current.map((row) => [row.id, row]));
  for (const row of incoming) {
    const existing = byId.get(row.id);
    if (!existing || !isRunning(row) || isRunning(existing)) byId.set(row.id, row);
  }
  return [...byId.values()].sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0)).slice(0, KEEP);
}

type Link = "connecting" | "live" | "reconnecting" | "off";

/**
 * The live log of tool calls: the recent ones from GET /api/mcp/activity, then every call
 * as it starts and ends from the stream. Rows are updated in place by id; only a row that
 * arrives after the first load rises in.
 */
export function Activity({ className }: { className?: string }) {
  const [rows, setRows] = useState<ActivityRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [link, setLink] = useState<Link>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    // Subscribe first, then read the list, so no call falls between the two.
    const subscription = subscribe<ActivityEvents>("/api/mcp/activity/stream", {
      on: {
        call: (row) => {
          if (alive && isRow(row)) setRows((current) => merge(current, [row]));
        },
      },
      onOpen: () => {
        if (!alive) return;
        setLink("live");
        api
          .get<ActivityRow[] | { items?: ActivityRow[] }>("/api/mcp/activity")
          .then((answer) => {
            if (!alive) return;
            const list = Array.isArray(answer) ? answer : (answer?.items ?? []);
            setRows((current) => merge(current, list.filter(isRow)));
            setError(null);
            setLoaded(true);
          })
          .catch((e: unknown) => {
            if (!alive) return;
            setError(errorMessage(e));
            setLoaded(true);
          });
      },
      onDisconnect: () => {
        if (alive) setLink("reconnecting");
      },
      onError: (e) => {
        if (!alive) return;
        setLink("off");
        setError(e.message);
        setLoaded(true);
      },
    });
    return () => {
      alive = false;
      subscription.close();
    };
  }, [attempt]);

  function retry() {
    setError(null);
    setLoaded(false);
    setLink("connecting");
    setAttempt((n) => n + 1);
  }

  return (
    <section aria-labelledby="activity-heading" className={className}>
      <div className="flex items-baseline justify-between gap-4">
        <h2 id="activity-heading" className="text-lg font-medium">
          Activity
        </h2>
        <p className="flex items-center gap-2 text-sm">
          <span
            aria-hidden
            className={cn(
              "size-1.5 rounded-full",
              link === "live" ? "bg-ok" : link === "off" ? "bg-danger" : "bg-warn",
            )}
          />
          <StatusWord tone="muted">
            {link === "live"
              ? "Live"
              : link === "off"
                ? "Offline"
                : link === "reconnecting"
                  ? "Reconnecting"
                  : "Connecting"}
          </StatusWord>
        </p>
      </div>

      {!loaded ? (
        <div className="mt-4 grid gap-3" aria-hidden>
          <Skeleton className="h-10" />
          <Skeleton className="h-10" />
          <Skeleton className="h-10" />
        </div>
      ) : error && rows.length === 0 ? (
        <div className="mt-3">
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
          <Button type="button" variant="secondary" className="mt-3" onClick={retry}>
            Try again
          </Button>
        </div>
      ) : rows.length === 0 ? (
        <p className="mt-3 max-w-[46ch] text-sm text-muted-foreground">
          No calls yet. Press Try on a tool, or connect an agent and ask it to list the reports:
          the call shows up here as it starts.
        </p>
      ) : (
        <ul
          aria-live="polite"
          aria-relevant="additions"
          className="no-scrollbar mt-2 lg:max-h-[calc(100dvh-13rem)] lg:overflow-y-auto"
        >
          <AnimatePresence initial={false}>
            {rows.map((row) => (
              <motion.li
                key={row.id}
                layout="position"
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
                className="grid grid-cols-[4.25rem_minmax(0,1fr)_auto] items-baseline gap-x-3 border-b border-line/70 py-3 last:border-b-0"
              >
                <Row row={row} />
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      )}
    </section>
  );
}

function Row({ row }: { row: ActivityRow }) {
  const running = isRunning(row);
  // The summary starts with the tool name ("classify: 3 labels, 61 characters"): keep the counts.
  const counts = row.summary?.startsWith(row.tool)
    ? row.summary.slice(row.tool.length).replace(/^[\s:]+/, "")
    : (row.summary ?? "");
  const summary = running ? "" : counts;
  return (
    <>
      <span className="text-sm">
        <time dateTime={row.at} className="block tabular-nums text-muted-foreground">
          {clockTime(row.at)}
        </time>
      </span>
      <span className="min-w-0">
        <span className="block truncate font-mono text-sm">{row.tool}</span>
        <span className="mt-0.5 block truncate text-xs text-muted-foreground">
          {row.client ?? "Unnamed client"}
        </span>
      </span>
      <span className="text-right text-sm">
        <StatusWord tone={running ? "accent" : row.ok ? "ok" : "danger"}>
          {running ? "Running" : row.ok ? "OK" : "Failed"}
        </StatusWord>
        <span className="mt-0.5 block text-xs tabular-nums text-muted-foreground">
          {running ? " " : duration(row.ms)}
        </span>
      </span>
      {summary && (
        <span className="col-start-2 col-end-4 mt-1 text-xs text-muted-foreground">{summary}</span>
      )}
    </>
  );
}
