"use client";

import { cn } from "cn";

import { StatusWord } from "@/components/shell";

import { duration } from "./text";
import type { StepView } from "./types";

const dot = {
  idle: "bg-line",
  running: "bg-accent animate-pulse",
  done: "bg-ok",
  error: "bg-danger",
} as const;

/**
 * The pipeline as connected blocks. A block rises in when its step arrives from the
 * generate stream; the run stream then changes its state in place (keyed by step id, so
 * nothing replays its entrance).
 */
export function Pipeline({ steps }: { steps: StepView[] }) {
  return (
    <ol aria-label="Pipeline" className="flex flex-col">
      {steps.map((step, index) => (
        <li key={step.id} className="relative animate-rise pb-2.5 pl-6 last:pb-0">
          {index < steps.length - 1 && (
            <span
              aria-hidden
              className="absolute top-6 -bottom-5 left-[4px] w-px bg-line animate-in fade-in duration-300"
            />
          )}
          <span
            aria-hidden
            className={cn(
              "absolute top-[1.15rem] left-0 size-[9px] rounded-full transition-colors duration-200",
              dot[step.state],
            )}
          />
          <div className="rounded-lg bg-surface px-3.5 py-3">
            <div className="flex items-baseline justify-between gap-3">
              <h3 className="min-w-0 text-[0.9375rem] font-medium">{step.title}</h3>
              {step.state !== "idle" && (
                <StatusWord
                  tone={step.state === "running" ? "accent" : step.state === "error" ? "danger" : "muted"}
                  className="shrink-0 text-[0.8125rem] tabular-nums"
                >
                  {step.state === "running"
                    ? "Running"
                    : step.state === "error"
                      ? "Failed"
                      : duration(step.ms) || "Done"}
                </StatusWord>
              )}
            </div>
            <p className="mt-0.5 text-[0.875rem] text-muted-foreground">{step.detail}</p>
            {step.note && (
              <p
                className={cn(
                  "mt-1 text-[0.8125rem]",
                  step.state === "error" ? "text-danger" : "text-muted-foreground",
                )}
              >
                {step.note}
              </p>
            )}
            <code className="mt-2 block font-mono text-[0.75rem] leading-snug text-muted-foreground [overflow-wrap:anywhere]">
              {step.gaik_import || step.component}
            </code>
          </div>
        </li>
      ))}
    </ol>
  );
}
