import type {
  FieldDef,
  StudioClass,
  StudioResult,
  StudioSpec,
  StudioStep,
} from "@/lib/types";

/** A stream may report a refusal as an event instead of an HTTP status. */
export interface StreamError {
  code?: string;
  message?: string;
}

/** The result of a run, with the notice line the API may add to it. */
export type RunResult = StudioResult & { notice?: string | null };

/** SSE events of POST /api/studio/generate (section 8.3), plus an optional `error`. */
export type GenerateEvents = {
  status: { text: string };
  step: { step: StudioStep };
  fields: { fields: FieldDef[] };
  classes: { classes: StudioClass[] };
  spec: { spec: StudioSpec };
  error: StreamError;
  done: unknown;
};

/** SSE events of POST /api/studio/run (section 8.3), plus an optional `error`. */
export type RunEvents = {
  step: { id: string; status: "running" | "done" | "error"; ms: number | null; note: string | null };
  result: RunResult;
  error: StreamError;
  done: unknown;
};

export type StepState = "idle" | "running" | "done" | "error";

/** A pipeline step as the page shows it: the spec's step plus what the run reported. */
export interface StepView extends StudioStep {
  state: StepState;
  ms: number | null;
  note: string | null;
}

export interface RunInput {
  files: File[];
  sampleId: string | null;
  question: string;
}

export interface RunState {
  state: "idle" | "running" | "done" | "error";
  result: RunResult | null;
  error: string | null;
}

export interface ExportState {
  loading: boolean;
  python: string | null;
  blueprint: string | null;
  error: string | null;
}
