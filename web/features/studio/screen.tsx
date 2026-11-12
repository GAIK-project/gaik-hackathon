"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp } from "lucide-react";
import { cn } from "cn";

import { Pressable, StatusWord } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { postStream } from "@/lib/sse";
import type { StudioExport, StudioSample, StudioSpec, StudioStep } from "@/lib/types";

import { ExportSheet } from "./export-sheet";
import { Pipeline } from "./pipeline";
import { Preview } from "./preview";
import { EXAMPLES, NOTICE_PERSONAL, errorText, streamErrorText } from "./text";
import type {
  ExportState,
  GenerateEvents,
  RunEvents,
  RunInput,
  RunResult,
  RunState,
  StepView,
} from "./types";

type Phase = "idle" | "building" | "ready" | "error";

const NO_RUN: RunState = { state: "idle", result: null, error: null };
const NO_EXPORT: ExportState = { loading: false, python: null, blueprint: null, error: null };

function toView(step: StudioStep): StepView {
  return { ...step, state: "idle", ms: null, note: null };
}

/**
 * GAIK Studio: a request becomes a spec (the pipeline arrives step by step), the spec is
 * rendered as a small app that runs with real gaik components, and the same pipeline can
 * be exported as code. One column on a phone: conversation, pipeline, preview.
 */
export function StudioScreen() {
  const [prompt, setPrompt] = useState("");
  const [request, setRequest] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [status, setStatus] = useState("Reading the request");
  const [buildError, setBuildError] = useState<string | null>(null);
  const [steps, setSteps] = useState<StepView[]>([]);
  const [spec, setSpec] = useState<StudioSpec | null>(null);
  const [run, setRun] = useState<RunState>(NO_RUN);
  const [samples, setSamples] = useState<StudioSample[]>([]);
  const [exportOpen, setExportOpen] = useState(false);
  const [exported, setExported] = useState<ExportState>(NO_EXPORT);
  const work = useRef<AbortController | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .get<StudioSample[]>("/api/studio/samples")
      .then((list) => {
        if (alive && Array.isArray(list)) setSamples(list);
      })
      .catch(() => {
        // The sample button still works: the server resolves the sample id itself.
      });
    return () => {
      alive = false;
      work.current?.abort();
    };
  }, []);

  async function generate(text: string) {
    const wanted = text.trim();
    if (!wanted) return;
    work.current?.abort();
    const controller = new AbortController();
    work.current = controller;

    setRequest(wanted);
    setPrompt("");
    setPhase("building");
    setStatus("Reading the request");
    setBuildError(null);
    setSteps([]);
    setSpec(null);
    setRun(NO_RUN);
    setExported(NO_EXPORT);

    const out: { spec: StudioSpec | null; refusal: string | null } = { spec: null, refusal: null };
    try {
      await postStream<GenerateEvents>(
        "/api/studio/generate",
        { json: { prompt: wanted }, signal: controller.signal },
        {
          on: {
            status: (data) => {
              if (data?.text) setStatus(data.text);
            },
            step: ({ step }) =>
              setSteps((prev) =>
                prev.some((item) => item.id === step.id) ? prev : [...prev, toView(step)],
              ),
            spec: ({ spec: built }) => {
              out.spec = built;
              setSpec(built);
              setSteps((prev) =>
                built.steps.map((step) => prev.find((item) => item.id === step.id) ?? toView(step)),
              );
            },
            error: (data) => {
              out.refusal = streamErrorText(data, "That description could not be turned into an app.");
            },
          },
        },
      );
    } catch (error) {
      if (controller.signal.aborted) return;
      setPhase("error");
      setBuildError(errorText(error));
      return;
    }
    if (controller.signal.aborted) return;
    if (out.spec) {
      setPhase("ready");
    } else {
      setPhase("error");
      setBuildError(
        out.refusal ??
          "That description could not be turned into an app. Say what goes in and what should come out.",
      );
    }
  }

  async function runSpec(input: RunInput) {
    if (!spec) return;
    work.current?.abort();
    const controller = new AbortController();
    work.current = controller;

    const form = new FormData();
    form.append("spec", JSON.stringify(spec));
    if (input.sampleId) form.append("sample_id", input.sampleId);
    else for (const file of input.files) form.append("files", file, file.name);
    if (input.question) form.append("question", input.question);

    setSteps((prev) => prev.map((step) => ({ ...step, state: "idle", ms: null, note: null })));
    setRun({ state: "running", result: null, error: null });

    const out: { result: RunResult | null; failure: string | null } = { result: null, failure: null };
    try {
      await postStream<RunEvents>(
        "/api/studio/run",
        { body: form, signal: controller.signal },
        {
          on: {
            step: (event) => {
              if (event.status === "error") {
                out.failure = event.note || "A step of the pipeline failed.";
              }
              setSteps((prev) =>
                prev.map((step) =>
                  step.id === event.id
                    ? { ...step, state: event.status, ms: event.ms, note: event.note }
                    : step,
                ),
              );
            },
            result: (result) => {
              out.result = result;
              setRun({ state: "running", result, error: null });
            },
            error: (data) => {
              out.failure = streamErrorText(data, "The run failed. Try again.");
            },
          },
        },
      );
    } catch (error) {
      if (controller.signal.aborted) return;
      out.failure = errorText(error);
    }
    if (controller.signal.aborted) return;

    // A step still marked as running did not finish.
    setSteps((prev) =>
      prev.map((step) => (step.state === "running" ? { ...step, state: out.result ? "done" : "error" } : step)),
    );
    if (out.result) setRun({ state: "done", result: out.result, error: null });
    else setRun({ state: "error", result: null, error: out.failure ?? "The run gave no result. Try again." });
  }

  function loadExport() {
    if (!spec) return;
    const current = spec;
    setExported({ ...NO_EXPORT, loading: true });
    api
      .post<StudioExport>("/api/studio/export", { spec: current })
      .then((data) =>
        setExported({
          loading: false,
          python: data.python,
          blueprint: JSON.stringify(data.blueprint, null, 2),
          error: null,
        }),
      )
      .catch((error: unknown) => setExported({ ...NO_EXPORT, error: errorText(error) }));
  }

  function openExport() {
    setExportOpen(true);
    if (exported.python === null && !exported.loading) loadExport();
  }

  const building = phase === "building";
  const runningTitle = steps.find((step) => step.state === "running")?.title ?? null;

  const promptBox = (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void generate(prompt);
      }}
      className="relative"
    >
      <Textarea
        value={prompt}
        onChange={(event) => setPrompt(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            void generate(prompt);
          }
        }}
        rows={request ? 2 : 3}
        maxLength={2000}
        aria-label="Describe the app"
        placeholder={request ? "Describe another app" : "Describe what goes in and what should come out"}
        className="min-h-0 resize-none rounded-xl bg-surface py-3.5 pr-14 pl-4 text-base"
      />
      <Button
        type="submit"
        size="icon-sm"
        disabled={!prompt.trim() || building}
        aria-label="Build the app"
        title="Build"
        className="absolute right-2.5 bottom-2.5"
      >
        <ArrowUp aria-hidden />
      </Button>
    </form>
  );

  if (request === null) {
    return (
      <div className="flex max-w-2xl flex-col gap-3">
        {promptBox}
        <div className="flex flex-wrap gap-2">
          {EXAMPLES.map((example) => (
            <Pressable
              key={example.label}
              onClick={() => void generate(example.prompt)}
              className="min-h-11 rounded-lg bg-surface px-3.5 text-[0.9375rem] text-muted-foreground hover:bg-surface-2 hover:text-fg"
            >
              {example.label}
            </Pressable>
          ))}
        </div>
        <p className="text-[0.8125rem] text-muted-foreground">{NOTICE_PERSONAL}</p>
      </div>
    );
  }

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,23rem)_minmax(0,1fr)] lg:items-start lg:gap-10">
      <div className="flex min-w-0 flex-col gap-6">
        <section aria-label="Conversation" className="flex flex-col gap-3">
          <p className="max-w-[90%] self-end rounded-xl bg-surface-2 px-3.5 py-2.5 text-[0.9375rem]">
            {request}
          </p>
          <div className="min-h-6 text-[0.9375rem]" aria-live="polite">
            {building && (
              <StatusWord tone="muted">{status}</StatusWord>
            )}
            {phase === "ready" && spec && (
              <p className="animate-rise">
                Here is {spec.title}. Run it with the sample or with your own{" "}
                {spec.input.kind === "audio" ? "recording" : "files"}.
              </p>
            )}
            {phase === "error" && (
              <p role="alert" className="animate-rise text-danger">
                {buildError}
              </p>
            )}
          </div>
          {!building && promptBox}
        </section>

        {steps.length > 0 && <Pipeline steps={steps} />}
      </div>

      <div className={cn("min-w-0", !spec && "max-lg:hidden")}>
        {spec ? (
          <Preview
            key={spec.id}
            spec={spec}
            samples={samples}
            run={run}
            runningTitle={runningTitle}
            onRun={(input) => void runSpec(input)}
            onExport={openExport}
          />
        ) : (
          building && (
            <div aria-hidden className="h-64 animate-pulse rounded-xl bg-surface/60" />
          )
        )}
      </div>

      <ExportSheet
        open={exportOpen}
        onOpenChange={setExportOpen}
        state={exported}
        onRetry={loadExport}
      />
    </div>
  );
}
