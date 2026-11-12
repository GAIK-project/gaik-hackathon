import type { Assessment, Citation, Obligation, Tier } from "@/lib/types";

/**
 * Wire types of the AI Act checker as amended by docs/ARCHITECTURE.md section 15.8.
 * `lib/types.ts` still holds the shape of section 8.4; these add the fields 15.8 gave.
 */

export interface AiActObligation extends Obligation {
  /** A date from Article 113 as amended, ISO ("2026-08-02") or already worded. */
  applies_from?: string | null;
}

export interface AiActAssessment extends Omit<Assessment, "obligations" | "language"> {
  obligations: AiActObligation[];
  language: string;
  also_transparency?: boolean;
  /** "02024R1689-20260727" */
  corpus_version?: string;
  derogation_note?: string | null;
}

export interface AiActStreamError {
  code: string;
  message: string;
}

/** SSE events of POST /api/ai-act/assess, with the `error` event of core/events. */
export type AiActAssessEvents = {
  step: {
    id: string;
    label: string;
    status: "running" | "done" | "error";
    ms: number | null;
    component: string | null;
  };
  partial: { probabilities: Partial<Record<Tier, number>> };
  result: { assessment: AiActAssessment };
  error: AiActStreamError;
  done: unknown;
};

/** A paragraph of a unit: plain text, or the corpus object with its number. */
export type AiActUnitParagraph = string | { n?: string | null; text: string };

/** GET /api/ai-act/units/{unit_id} */
export interface AiActUnitDetail {
  id: string;
  kind: string;
  label: string;
  title: string | null;
  text: string;
  paragraphs?: AiActUnitParagraph[];
}

export type { Citation, Tier };
