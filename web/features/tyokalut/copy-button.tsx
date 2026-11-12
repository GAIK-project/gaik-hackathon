"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "cn";

import { Button } from "@/components/ui/button";

async function writeClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // No clipboard permission (an http origin, an old browser): the selection route.
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    area.remove();
    return ok;
  }
}

/** Copies `text`; the icon turns into a check for a moment, and the name says "Copied". */
export function CopyButton({
  text,
  label,
  className,
}: {
  text: string;
  /** What is copied, as the control's name: "Copy the MCP address". */
  label: string;
  className?: string;
}) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(timer.current), []);

  async function copy() {
    const ok = await writeClipboard(text);
    setState(ok ? "copied" : "failed");
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 1600);
  }

  const name = state === "copied" ? "Copied" : state === "failed" ? "Copying failed" : label;

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      onClick={copy}
      aria-label={name}
      title={name}
      className={cn("text-muted-foreground hover:text-fg", className)}
    >
      <span key={state} className="animate-rise">
        {state === "copied" ? (
          <Check className="size-4 text-ok" aria-hidden />
        ) : (
          <Copy className={cn("size-4", state === "failed" && "text-danger")} aria-hidden />
        )}
      </span>
      <span className="sr-only" role="status">
        {state === "idle" ? "" : name}
      </span>
    </Button>
  );
}
