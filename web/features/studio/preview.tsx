"use client";

import { useState } from "react";
import { AudioLines, Braces, FileText, LoaderCircle, Play, Upload, X } from "lucide-react";
import { cn } from "cn";

import { Pressable, StackLogo, StatusWord } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { StudioSample, StudioSpec } from "@/lib/types";

import { ResultView } from "./results";
import { bytes, checkFiles, noticesFor } from "./text";
import type { RunInput, RunState } from "./types";

function FileMark({ name, audio }: { name: string; audio: boolean }) {
  const extension = name.split(".").pop()?.toLowerCase();
  if (extension === "pdf") return <StackLogo id="pdf" size={18} />;
  if (extension === "docx") return <StackLogo id="word" size={18} />;
  const Icon = audio ? AudioLines : FileText;
  return <Icon aria-hidden className="size-[18px] shrink-0 text-muted-foreground" strokeWidth={1.75} />;
}

/**
 * The generated app, rendered from the spec: what it takes in, a run button and the result
 * in the shape of the template. Mount it with `key={spec.id}` so a new spec starts clean.
 */
export function Preview({
  spec,
  samples,
  run,
  runningTitle,
  onRun,
  onExport,
}: {
  spec: StudioSpec;
  samples: StudioSample[];
  run: RunState;
  /** Title of the step that is running, shown next to the run button. */
  runningTitle: string | null;
  onRun: (input: RunInput) => void;
  onExport: () => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [sampleId, setSampleId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [over, setOver] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);

  const audio = spec.input.kind === "audio";
  const asksQuestion = spec.template === "documents_to_answers";
  const running = run.state === "running";
  const firstSampleId = spec.input.sample_ids[0] ?? null;
  const sample = sampleId ? samples.find((item) => item.id === sampleId) : undefined;
  const hasInput = files.length > 0 || sampleId !== null;
  const canRun = hasInput && !running && (!asksQuestion || question.trim().length > 0);

  function takeFiles(list: FileList | null) {
    if (!list || list.length === 0) return;
    const picked = Array.from(list);
    const next = spec.input.multiple ? [...files, ...picked] : picked.slice(0, 1);
    const problem = checkFiles(spec, next);
    setFileError(problem);
    if (problem) return;
    setSampleId(null);
    setFiles(next);
  }

  function trySample() {
    if (!firstSampleId) return;
    const ask = asksQuestion ? question.trim() || spec.question_examples[0] || "" : "";
    setFiles([]);
    setFileError(null);
    setSampleId(firstSampleId);
    if (asksQuestion) setQuestion(ask);
    if (!asksQuestion || ask) onRun({ files: [], sampleId: firstSampleId, question: ask });
  }

  const listed = sample
    ? sample.files.map((file) => ({ key: file.name, name: file.name, size: file.size }))
    : files.map((file, index) => ({ key: `${file.name}-${index}`, name: file.name, size: file.size }));

  return (
    <section
      aria-label="Preview"
      className="animate-rise rounded-xl bg-surface p-4 sm:p-6"
    >
      <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 sm:flex-nowrap">
        <div className="min-w-0">
          <h2 className="text-[1.375rem] leading-tight font-medium tracking-[-0.01em]">{spec.title}</h2>
          <p className="mt-1 max-w-[60ch] text-[0.9375rem] text-muted-foreground">{spec.summary}</p>
        </div>
        <Button variant="ghost" size="sm" onClick={onExport} className="shrink-0 sm:-mr-2 max-sm:-ml-3">
          <Braces aria-hidden />
          Export as code
        </Button>
      </header>

      <form
        className="mt-5 flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (canRun) onRun({ files, sampleId, question: question.trim() });
        }}
      >
        {listed.length === 0 ? (
          <label
            data-over={over}
            onDragOver={(event) => {
              event.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={(event) => {
              event.preventDefault();
              setOver(false);
              takeFiles(event.dataTransfer.files);
            }}
            className={cn(
              "press flex min-h-28 cursor-pointer flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-line px-4 py-5 text-center",
              "transition-[transform,background-color,border-color] duration-150 ease-stage hover:bg-surface-2",
              "focus-within:border-accent data-[over=true]:border-accent data-[over=true]:bg-surface-2",
            )}
          >
            <Upload aria-hidden className="size-5 text-muted-foreground" strokeWidth={1.75} />
            <span className="text-[0.9375rem]">{spec.input.hint}</span>
            <span className="text-[0.8125rem] text-muted-foreground">
              Drop {spec.input.multiple ? "files" : "a file"} here or press to choose
            </span>
            <input
              type="file"
              className="sr-only"
              accept={spec.input.accept.join(",")}
              multiple={spec.input.multiple}
              onChange={(event) => {
                takeFiles(event.target.files);
                event.target.value = "";
              }}
            />
          </label>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {listed.map((file, index) => (
              <li
                key={file.key}
                className="flex min-h-11 items-center gap-2.5 rounded-lg bg-surface-2 pr-1 pl-3 text-[0.875rem]"
              >
                <FileMark name={file.name} audio={audio} />
                <span className="min-w-0 flex-1 truncate">{file.name}</span>
                <span className="shrink-0 tabular-nums text-muted-foreground">{bytes(file.size)}</span>
                {!sample && (
                  <Pressable
                    aria-label={`Remove ${file.name}`}
                    title="Remove"
                    disabled={running}
                    onClick={() => setFiles(files.filter((_, at) => at !== index))}
                    className="grid size-9 shrink-0 place-items-center rounded-md text-muted-foreground hover:text-fg"
                  >
                    <X aria-hidden className="size-4" />
                  </Pressable>
                )}
              </li>
            ))}
            {sample && (
              <li>
                <Pressable
                  disabled={running}
                  onClick={() => setSampleId(null)}
                  className="min-h-9 rounded-md text-[0.8125rem] text-muted-foreground hover:text-fg"
                >
                  Use my own {audio ? "recording" : "files"} instead
                </Pressable>
              </li>
            )}
          </ul>
        )}
        {listed.length === 0 && sampleId && (
          <p className="text-[0.875rem] text-muted-foreground">The built-in sample is loaded.</p>
        )}
        {fileError && (
          <p role="alert" className="text-[0.875rem] text-danger">
            {fileError}
          </p>
        )}

        {asksQuestion && (
          <div className="flex flex-col gap-2">
            <Input
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="Ask the documents a question"
              aria-label="Question"
              maxLength={500}
              className="h-11"
            />
            {spec.question_examples.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {spec.question_examples.slice(0, 3).map((example) => (
                  <Pressable
                    key={example}
                    onClick={() => setQuestion(example)}
                    className="min-h-9 rounded-lg bg-surface-2 px-3 py-1.5 text-left text-[0.8125rem] text-muted-foreground hover:text-fg"
                  >
                    {example}
                  </Pressable>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <Button type="submit" disabled={!canRun} className="min-w-24">
            {running ? (
              <LoaderCircle aria-hidden className="animate-spin" />
            ) : (
              <Play aria-hidden className="fill-current" />
            )}
            Run
          </Button>
          {firstSampleId && (
            <Button type="button" variant="secondary" disabled={running} onClick={trySample}>
              Try with the sample
            </Button>
          )}
          {running && (
            <StatusWord tone="muted" className="text-[0.875rem]">
              {runningTitle ?? "Starting"}
            </StatusWord>
          )}
        </div>
        <p className="text-[0.8125rem] text-muted-foreground">{noticesFor(spec).join(" ")}</p>
      </form>

      {run.error && (
        <p role="alert" className="mt-5 animate-rise text-danger">
          {run.error}
        </p>
      )}
      {run.result && (
        <div className="mt-6 animate-rise border-t border-line pt-5" aria-live="polite">
          <ResultView result={run.result} />
          {run.result.notice && !noticesFor(spec).includes(run.result.notice) && (
            <p className="mt-4 text-[0.8125rem] text-muted-foreground">{run.result.notice}</p>
          )}
        </div>
      )}
    </section>
  );
}
