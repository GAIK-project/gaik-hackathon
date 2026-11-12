"use client";

import { useEffect, useId, useRef, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { cn } from "cn";

import { Pressable } from "@/components/shell/pressable";
import { StatusWord } from "@/components/shell/status-word";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { postStream } from "@/lib/sse";
import type { AiActExample, Citation, Tier } from "@/lib/types";

import { failureMessage } from "./copy";
import { EvalGrid } from "./eval-grid";
import { AssessmentDetail, TierLadder } from "./result";
import type { AiActAssessEvents, AiActAssessment } from "./types";
import { UnitSheet } from "./unit-sheet";

/** Jev takes at most this many characters (docs/ARCHITECTURE.md section 15.6). */
const MAX_LENGTH = 2000;

type Probabilities = Partial<Record<Tier, number>>;

/**
 * The AI Act checker: a description goes in, the likely risk tier, its basis in the
 * regulation and the obligations come out. One column that reads top to bottom.
 */
export function AiActScreen() {
  const fieldId = useId();
  const [description, setDescription] = useState("");
  const [examples, setExamples] = useState<AiActExample[] | null>(null);
  const [running, setRunning] = useState(false);
  const [stepLabel, setStepLabel] = useState("");
  const [probabilities, setProbabilities] = useState<Probabilities | null>(null);
  const [assessment, setAssessment] = useState<AiActAssessment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [citation, setCitation] = useState<Citation | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const resultRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api
      .get<AiActExample[]>("/api/ai-act/examples", { signal: controller.signal })
      .then((list) => setExamples(Array.isArray(list) ? list : []))
      .catch(() => {
        // The examples are a shortcut: without them the field still works.
        if (!controller.signal.aborted) setExamples([]);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => () => controllerRef.current?.abort(), []);

  async function run() {
    const text = description.trim();
    if (!text || running) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;

    setRunning(true);
    setError(null);
    setAssessment(null);
    setProbabilities(null);
    setStepLabel("Starting");

    const outcome: { error: string | null; received: boolean } = { error: null, received: false };
    try {
      await postStream<AiActAssessEvents>(
        "/api/ai-act/assess",
        { json: { description: text }, signal: controller.signal },
        {
          on: {
            step: (step) => {
              if (step.status === "running" && step.label) setStepLabel(step.label);
              const estimate = (step as { probabilities?: Probabilities | null }).probabilities;
              if (estimate) setProbabilities(estimate);
            },
            partial: (partial) => {
              if (partial?.probabilities) setProbabilities(partial.probabilities);
            },
            result: ({ assessment: result }) => {
              outcome.received = true;
              setProbabilities(result.probabilities);
              setAssessment(result);
            },
            error: (failure) => {
              outcome.error = failure?.message || "The assessment failed. Try again.";
            },
          },
        },
      );
      if (controller.signal.aborted) return;
      if (outcome.error) setError(outcome.error);
      else if (!outcome.received) setError("The server sent no assessment. Try again.");
      else {
        const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        requestAnimationFrame(() =>
          resultRef.current?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" }),
        );
      }
    } catch (thrown) {
      if (!controller.signal.aborted) setError(outcome.error ?? failureMessage(thrown));
    } finally {
      if (controllerRef.current === controller) {
        setRunning(false);
        setStepLabel("");
      }
    }
  }

  const canRun = description.trim().length > 0 && !running;
  const showLadder = probabilities !== null && !error;

  return (
    <div className="max-w-3xl">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void run();
        }}
      >
        <label htmlFor={fieldId} className="block text-[0.9375rem] font-medium">
          Describe the AI system
        </label>
        <Textarea
          id={fieldId}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          maxLength={MAX_LENGTH}
          rows={5}
          placeholder="What it does, who uses it and whom its output affects"
          className="mt-2 min-h-36 bg-surface leading-relaxed dark:bg-surface"
        />

        {examples === null ? (
          <div className="mt-3 flex flex-wrap gap-2" aria-hidden>
            {[7, 9, 6, 8, 7, 9].map((width, index) => (
              <div
                key={index}
                className="h-10 animate-pulse rounded-lg bg-surface"
                style={{ width: `${width}rem` }}
              />
            ))}
          </div>
        ) : (
          examples.length > 0 && (
            <ul className="mt-3 flex flex-wrap gap-2" aria-label="Example systems">
              {examples.map((example) => {
                const active = description.trim() === example.description.trim();
                return (
                  <li key={example.id} className="max-w-full">
                    <Pressable
                      aria-pressed={active}
                      onClick={() => setDescription(example.description)}
                      className={cn(
                        "h-10 max-w-full truncate rounded-lg px-3 text-[0.8125rem]",
                        active
                          ? "bg-surface-2 text-fg"
                          : "bg-surface text-muted-foreground hover:bg-surface-2 hover:text-fg",
                      )}
                    >
                      {example.title}
                    </Pressable>
                  </li>
                );
              })}
            </ul>
          )
        )}

        <p className="mt-4 text-[0.8125rem] leading-relaxed text-muted-foreground">
          Do not share personal data. The description is also processed in the United States.
        </p>

        <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-4">
          <Button
            type="submit"
            size="lg"
            disabled={!running && !canRun}
            aria-busy={running}
            className={cn("w-full sm:w-auto", running && "pointer-events-none")}
          >
            {running && <LoaderCircle aria-hidden className="animate-spin" />}
            {running ? "Checking" : "Check the risk tier"}
          </Button>
          {/* The slot keeps its height, so the step line swaps without moving the page. */}
          <div className="flex min-h-6 min-w-0 items-center text-[0.9375rem]">
            {running && stepLabel && (
              <StatusWord tone="muted" className="max-w-full">
                {stepLabel}
              </StatusWord>
            )}
          </div>
        </div>

        {error && (
          <p role="alert" className="mt-4 max-w-[62ch] text-[0.9375rem] text-danger">
            {error}
          </p>
        )}
      </form>

      {showLadder && (
        <section
          ref={resultRef}
          aria-labelledby="ai-act-tier"
          className="mt-12 scroll-mt-6 animate-rise"
        >
          <h2 id="ai-act-tier" className="text-xl font-medium">
            Likely risk tier
          </h2>
          {assessment?.needs_human_review && (
            <p className="mt-3 max-w-[62ch] text-[0.9375rem] leading-relaxed text-warn">
              A person should review this result: the two methods disagreed or the estimate is
              uncertain.
            </p>
          )}
          <TierLadder probabilities={probabilities ?? {}} chosen={assessment?.tier ?? null} />
          {assessment && (
            <AssessmentDetail assessment={assessment} onOpenCitation={setCitation} />
          )}
        </section>
      )}

      <EvalGrid />

      <UnitSheet citation={citation} onClose={() => setCitation(null)} />
    </div>
  );
}
