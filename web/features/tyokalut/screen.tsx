"use client";

import { useEffect, useState } from "react";

import { StackLogo } from "@/components/shell/stack-logo";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, errorMessage } from "@/lib/api";

import { Activity } from "./activity";
import { Connect } from "./connect";
import { CopyButton } from "./copy-button";
import { absoluteUrl } from "./lib";
import { Tools } from "./tools";
import type { Info } from "./types";

type Load = { status: "loading" } | { status: "ready"; info: Info } | { status: "error"; message: string };

/**
 * Tools for agents: the MCP address, a snippet per ready-made client, the server's tools
 * with a "Try" each, and the live log of calls. Everything shown comes from
 * GET /api/mcp/info and the activity endpoints; the key is never on the page.
 */
export function TyokalutScreen() {
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    api
      .get<Info>("/api/mcp/info")
      .then((info) => {
        if (!alive) return;
        setLoad({
          status: "ready",
          info: {
            url: typeof info?.url === "string" ? info.url : "/mcp",
            notices: Array.isArray(info?.notices) ? info.notices : [],
            tools: Array.isArray(info?.tools) ? info.tools : [],
            clients: Array.isArray(info?.clients) ? info.clients : [],
          },
        });
      })
      .catch((error: unknown) => {
        if (alive) setLoad({ status: "error", message: errorMessage(error) });
      });
    return () => {
      alive = false;
    };
  }, [attempt]);

  if (load.status === "error") {
    return (
      <div>
        <p role="alert" className="max-w-[62ch] text-danger">
          {load.message}
        </p>
        <Button
          type="button"
          variant="secondary"
          className="mt-4"
          onClick={() => {
            setLoad({ status: "loading" });
            setAttempt((n) => n + 1);
          }}
        >
          Try again
        </Button>
      </div>
    );
  }

  const info = load.status === "ready" ? load.info : null;
  const address = info ? absoluteUrl(info.url) : "";

  return (
    <div className="flex flex-col gap-9 sm:gap-11">
      <div>
        <p className="max-w-[58ch] text-[1.0625rem] text-muted-foreground">
          The components behind these demos are also MCP tools, so a ready-made agent can parse,
          extract, classify and file reports with them.
        </p>
        <div className="mt-4 flex max-w-xl items-center gap-3 rounded-xl bg-surface py-1 pr-1 pl-4">
          <StackLogo id="mcp" size={18} label="MCP address" />
          {info ? (
            <>
              <code className="no-scrollbar min-w-0 flex-1 overflow-x-auto font-mono text-sm whitespace-nowrap">
                {address}
              </code>
              <CopyButton text={address} label="Copy the MCP address" />
            </>
          ) : (
            <Skeleton className="my-3 h-5 flex-1" />
          )}
        </div>
      </div>

      <div className="grid gap-9 sm:gap-11 lg:grid-cols-[minmax(0,1fr)_minmax(0,23rem)] lg:gap-14">
        <div className="flex min-w-0 flex-col gap-9 sm:gap-11">
          {info ? (
            <>
              <Connect clients={info.clients} />
              <Tools tools={info.tools} notices={info.notices} />
            </>
          ) : (
            <div className="grid gap-3" aria-busy="true" aria-label="Loading the tools">
              <Skeleton className="h-11 max-w-md" />
              <Skeleton className="h-28" />
              <Skeleton className="mt-6 h-14" />
              <Skeleton className="h-14" />
              <Skeleton className="h-14" />
            </div>
          )}
        </div>
        <Activity className="min-w-0 self-start lg:sticky lg:top-10" />
      </div>
    </div>
  );
}
