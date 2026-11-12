"use client";

import { useEffect, useState } from "react";
import { cn } from "cn";

import { Button } from "@/components/ui/button";
import { ApiError, api } from "@/lib/api";
import type { AiActEval } from "@/lib/types";

import { TIER_LABEL, failureMessage, formatRunDate } from "./copy";

type EvalState =
  | { status: "loading" }
  | { status: "ready"; data: AiActEval }
  | { status: "error"; message: string };

const GRID = "grid grid-cols-9 gap-1 sm:grid-cols-[repeat(18,minmax(0,1fr))]";

/**
 * "How it was tested": one square per test description, the wrong answers in the danger
 * colour, the score in words. A press on a square shows the case with the expected and
 * the given tier.
 */
export function EvalGrid() {
  const [state, setState] = useState<EvalState>({ status: "loading" });
  const [selected, setSelected] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api
      .get<AiActEval>("/api/ai-act/eval", { signal: controller.signal })
      .then((data) => {
        setState({ status: "ready", data });
        // Open on the most telling case: the first wrong answer, when there is one.
        setSelected((current) => current ?? data.cases.find((item) => !item.ok)?.id ?? null);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        // No stored run yet: an empty state, not a failure.
        if (error instanceof ApiError && error.status === 404) {
          setState({ status: "ready", data: { ran_at: "", total: 0, correct: 0, cases: [] } });
          return;
        }
        setState({ status: "error", message: failureMessage(error) });
      });
    return () => controller.abort();
  }, [attempt]);

  const data = state.status === "ready" ? state.data : null;
  const cases = data?.cases ?? [];
  const current = cases.find((item) => item.id === selected) ?? null;
  const total = data?.total ?? cases.length;
  const correct = data?.correct ?? cases.filter((item) => item.ok).length;
  const ranOn = formatRunDate(data?.ran_at);

  return (
    <section aria-labelledby="ai-act-eval" className="mt-14 border-t border-line pt-8">
      <h2 id="ai-act-eval" className="text-xl font-medium">
        How it was tested
      </h2>

      {state.status === "loading" && (
        <div className="mt-4" aria-label="Loading the test results">
          <div className="h-6 w-64 max-w-full animate-pulse rounded-md bg-surface-2" />
          <div className={cn(GRID, "mt-5")}>
            {Array.from({ length: 36 }, (_, index) => (
              <div key={index} className="aspect-square animate-pulse rounded-[5px] bg-surface-2" />
            ))}
          </div>
        </div>
      )}

      {state.status === "error" && (
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <p role="alert" className="text-[0.9375rem] text-danger">
            {state.message}
          </p>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              setState({ status: "loading" });
              setAttempt((value) => value + 1);
            }}
          >
            Try again
          </Button>
        </div>
      )}

      {data && cases.length === 0 && (
        <p className="mt-4 text-[0.9375rem] text-muted-foreground">
          The test set has not been run on this server yet.
        </p>
      )}

      {data && cases.length > 0 && (
        <>
          <p className="mt-3 max-w-[58ch] text-[1.0625rem] leading-relaxed">
            <span className="font-medium">
              {correct} of {total}
            </span>{" "}
            test descriptions got the risk tier a person had assigned beforehand
            {total - correct > 0 ? `; ${total - correct} got another one` : ""}.
            {ranOn && <span className="text-muted-foreground"> Run on {ranOn}.</span>}
          </p>

          <ul className={cn(GRID, "mt-5")}>
            {cases.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  aria-pressed={item.id === selected}
                  aria-label={`${item.title}: expected ${TIER_LABEL[item.expected] ?? item.expected}, given ${TIER_LABEL[item.got] ?? item.got}`}
                  onClick={() => setSelected(item.id)}
                  className={cn(
                    "press block aspect-square w-full cursor-pointer touch-manipulation rounded-[5px] outline-none",
                    "transition-[transform,opacity] duration-150 ease-stage focus-visible:ring-2 focus-visible:ring-accent",
                    item.ok ? "bg-fg/22" : "bg-danger",
                    item.id === selected
                      ? "opacity-100 ring-2 ring-fg ring-offset-2 ring-offset-bg"
                      : "opacity-80 hover:opacity-100",
                  )}
                />
              </li>
            ))}
          </ul>

          <div className="mt-4 min-h-[4.25rem]" aria-live="polite">
            {current ? (
              <div key={current.id} className="animate-rise">
                <p className="text-[0.9375rem] font-medium">{current.title}</p>
                <p className="mt-1 text-[0.875rem] text-muted-foreground">
                  {current.ok ? (
                    <>Expected and given: {TIER_LABEL[current.expected] ?? current.expected}</>
                  ) : (
                    <>
                      Expected {TIER_LABEL[current.expected] ?? current.expected},{" "}
                      <span className="text-danger">
                        given {TIER_LABEL[current.got] ?? current.got}
                      </span>
                    </>
                  )}
                </p>
              </div>
            ) : (
              <p className="text-[0.875rem] text-muted-foreground">
                Press a square to see the case and its tiers.
              </p>
            )}
          </div>
        </>
      )}
    </section>
  );
}
