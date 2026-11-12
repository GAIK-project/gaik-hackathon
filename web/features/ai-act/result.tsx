"use client";

import { useState } from "react";
import { Download, LoaderCircle, TriangleAlert } from "lucide-react";
import { cn } from "cn";

import { SourceChip } from "@/components/shell/source-chip";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import type { Citation, Tier } from "@/lib/types";

import { TIERS, TIER_LABEL, WHO_LABEL, failureMessage, formatAppliesFrom, percent } from "./copy";
import type { AiActAssessment } from "./types";

/**
 * The four tiers as a ladder, the most restricted on top. Each rung carries its
 * probability as a bar drawn from the data; the chosen tier is lifted. The rungs stay in
 * place while the stream fills them in.
 */
export function TierLadder({
  probabilities,
  chosen,
}: {
  probabilities: Partial<Record<Tier, number>>;
  chosen: Tier | null;
}) {
  return (
    <ol className="mt-4 space-y-1.5">
      {TIERS.map((tier) => {
        const value = percent(probabilities[tier]);
        const isChosen = tier === chosen;
        return (
          <li
            key={tier}
            aria-current={isChosen ? "true" : undefined}
            className={cn(
              "relative origin-left rounded-xl px-4 py-3 transition-transform duration-300 ease-stage",
              isChosen && "z-1 sm:scale-[1.02]",
            )}
          >
            <span
              aria-hidden
              className={cn(
                "absolute inset-0 rounded-xl bg-surface-2 shadow-glow transition-opacity duration-300 ease-stage",
                isChosen ? "opacity-100" : "opacity-0",
              )}
            />
            <span className="relative flex items-baseline justify-between gap-3">
              <span
                className={cn(
                  "text-[0.9375rem]",
                  isChosen ? "text-[1.0625rem] font-medium text-fg" : "text-muted-foreground",
                )}
              >
                {TIER_LABEL[tier]}
              </span>
              <span
                className={cn(
                  "text-[0.875rem] tabular-nums",
                  isChosen ? "font-medium text-fg" : "text-muted-foreground",
                )}
              >
                {value}%
              </span>
            </span>
            <span
              aria-hidden
              className={cn(
                "relative mt-2 block h-1.5 overflow-hidden rounded-full",
                isChosen ? "bg-line" : "bg-surface-2",
              )}
            >
              <span
                className={cn(
                  "block h-full origin-left rounded-full transition-transform duration-500 ease-stage",
                  isChosen ? "bg-accent" : "bg-muted-foreground/55",
                )}
                style={{ transform: `scaleX(${value / 100})` }}
              />
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function CitationChip({ citation, onOpen }: { citation: Citation; onOpen: (c: Citation) => void }) {
  const unverified = Boolean(citation.quote) && !citation.verified;
  return (
    <SourceChip
      label={citation.label}
      onPress={() => onOpen(citation)}
      className="h-9 px-2.5"
      icon={
        unverified ? (
          <TriangleAlert aria-label="Quote not verified" className="size-3.5 shrink-0 text-warn" strokeWidth={1.75} />
        ) : undefined
      }
    />
  );
}

function ReportButton({ assessmentId }: { assessmentId: string }) {
  const [state, setState] = useState<"idle" | "working">("idle");
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setState("working");
    setError(null);
    try {
      const response = await apiFetch("/api/ai-act/report", {
        method: "POST",
        json: { assessment_id: assessmentId },
        headers: { Accept: "*/*" },
      });
      const blob = await response.blob();
      const named = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(
        response.headers.get("Content-Disposition") ?? "",
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = named ? decodeURIComponent(named[1]) : "ai-act-assessment.docx";
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (thrown) {
      setError(failureMessage(thrown));
    } finally {
      setState("idle");
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <Button variant="secondary" onClick={download} disabled={state === "working"}>
        {state === "working" ? (
          <LoaderCircle aria-hidden className="animate-spin" />
        ) : (
          <Download aria-hidden />
        )}
        {state === "working" ? "Writing the report" : "Download report"}
      </Button>
      {error && (
        <p role="alert" className="text-[0.875rem] text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

/** Everything under the ladder: notes, basis, obligations, the report and the foot. */
export function AssessmentDetail({
  assessment,
  onOpenCitation,
}: {
  assessment: AiActAssessment;
  onOpenCitation: (citation: Citation) => void;
}) {
  const disclaimer = assessment.disclaimer?.trim() ?? "";
  const foot = /legal advice/i.test(disclaimer)
    ? disclaimer
    : `This is not legal advice.${disclaimer ? ` ${disclaimer}` : ""}`;

  return (
    <div className="animate-rise">
      {(assessment.also_transparency || assessment.derogation_note) && (
        <div className="mt-4 space-y-1.5 px-4 text-[0.9375rem] text-muted-foreground">
          {assessment.also_transparency && (
            <p>The transparency obligations of Article 50 apply as well.</p>
          )}
          {assessment.derogation_note && <p>{assessment.derogation_note}</p>}
        </div>
      )}

      <section aria-labelledby="ai-act-basis" className="mt-10">
        <h2 id="ai-act-basis" className="text-xl font-medium">
          Basis
        </h2>
        <p className="mt-3 max-w-[62ch] leading-relaxed text-fg/90">{assessment.reasoning}</p>
        {assessment.basis.length > 0 && (
          <ul className="mt-4 flex flex-wrap gap-2">
            {assessment.basis.map((citation, index) => (
              <li key={`${citation.unit_id}-${index}`} className="max-w-full">
                <CitationChip citation={citation} onOpen={onOpenCitation} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="ai-act-obligations" className="mt-10">
        <h2 id="ai-act-obligations" className="text-xl font-medium">
          Obligations
        </h2>
        {assessment.obligations.length === 0 ? (
          <p className="mt-3 max-w-[62ch] text-muted-foreground">
            The regulation sets no specific obligations for this tier.
          </p>
        ) : (
          <ul className="mt-2 divide-y divide-line">
            {assessment.obligations.map((obligation, index) => {
              const from = formatAppliesFrom(obligation.applies_from);
              return (
                <li key={`${obligation.citation.unit_id}-${index}`} className="py-4">
                  <p className="font-medium">{obligation.title}</p>
                  <p className="mt-1 max-w-[62ch] text-[0.9375rem] leading-relaxed text-muted-foreground">
                    {obligation.summary}
                  </p>
                  <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-[0.8125rem]">
                    <span>{WHO_LABEL[obligation.who] ?? obligation.who}</span>
                    {from && <span className="text-muted-foreground">Applies from {from}</span>}
                    <span className="max-w-full sm:ml-auto">
                      <CitationChip citation={obligation.citation} onOpen={onOpenCitation} />
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <div className="mt-8">
        <ReportButton assessmentId={assessment.id} />
      </div>

      <p className="mt-8 max-w-[62ch] text-[0.8125rem] leading-relaxed text-muted-foreground">
        {foot}
        {assessment.corpus_version && (
          <>
            {" "}
            Text of the regulation used: {assessment.corpus_version.replace(/\.$/, "")}.
          </>
        )}
      </p>
    </div>
  );
}
