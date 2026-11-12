"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Check, TriangleAlert, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { Citation } from "@/lib/types";

import { failureMessage } from "./copy";
import type { AiActUnitDetail } from "./types";

type Range = [start: number, end: number];

/** Lower-cased text with runs of white space collapsed, and where each character came from. */
function normalise(text: string): { norm: string; map: number[] } {
  let norm = "";
  const map: number[] = [];
  let pendingSpace = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (/\s/.test(char)) {
      pendingSpace = norm.length > 0;
      continue;
    }
    if (pendingSpace) {
      norm += " ";
      map.push(index - 1);
      pendingSpace = false;
    }
    const lower = char.toLowerCase().replace(/[‘’]/g, "'").replace(/[“”]/g, '"');
    for (const piece of lower) {
      norm += piece;
      map.push(index);
    }
  }
  return { norm, map };
}

/**
 * Where the quoted words sit in the unit's text. A quote with an ellipsis is looked up
 * piece by piece. Differences in white space, case and curly quotes do not matter.
 */
export function quoteRanges(text: string, quote: string): Range[] {
  const { norm, map } = normalise(text);
  const ranges: Range[] = [];
  for (const piece of quote.split(/\s*(?:\[\s*(?:\.\.\.|…)\s*\]|\.\.\.|…)\s*/)) {
    const needle = normalise(piece).norm.replace(/^["']+|["']+$/g, "");
    if (needle.length < 8) continue;
    const at = norm.indexOf(needle);
    if (at === -1) continue;
    ranges.push([map[at], map[at + needle.length - 1] + 1]);
  }
  return ranges.sort((a, b) => a[0] - b[0]);
}

function unitText(unit: AiActUnitDetail): string {
  if (unit.text) return unit.text;
  return (unit.paragraphs ?? [])
    .map((paragraph) => (typeof paragraph === "string" ? paragraph : paragraph.text))
    .join("\n");
}

/** The unit's text as paragraphs, the quoted words marked. */
function MarkedText({ text, ranges }: { text: string; ranges: Range[] }) {
  const firstMark = useRef<HTMLElement | null>(null);

  useEffect(() => {
    firstMark.current?.scrollIntoView({ block: "center" });
  }, [text]);

  const paragraphs: React.ReactNode[] = [];
  let offset = 0;
  let marked = false;
  text.split("\n").forEach((line, lineIndex) => {
    const start = offset;
    const end = offset + line.length;
    offset = end + 1;
    if (!line.trim()) return;
    const parts: React.ReactNode[] = [];
    let cursor = start;
    for (const [from, to] of ranges) {
      const a = Math.max(from, start);
      const b = Math.min(to, end);
      if (a >= b) continue;
      if (a > cursor) parts.push(text.slice(cursor, a));
      const isFirst = !marked;
      marked = true;
      parts.push(
        <mark
          key={a}
          ref={isFirst ? firstMark : undefined}
          className="rounded-[3px] bg-accent/25 box-decoration-clone px-0.5 text-fg"
        >
          {text.slice(a, b)}
        </mark>,
      );
      cursor = b;
    }
    if (cursor < end) parts.push(text.slice(cursor, end));
    paragraphs.push(<p key={lineIndex}>{parts}</p>);
  });

  return <div className="space-y-3 text-[0.9375rem] leading-relaxed text-fg/85">{paragraphs}</div>;
}

const WIDE = "(min-width: 640px)";

function useIsWide(): boolean {
  return useSyncExternalStore(
    (notify) => {
      const query = window.matchMedia(WIDE);
      query.addEventListener("change", notify);
      return () => query.removeEventListener("change", notify);
    },
    () => window.matchMedia(WIDE).matches,
    () => false,
  );
}

const unitCache = new Map<string, AiActUnitDetail>();

type UnitState =
  | { status: "loading" }
  | { status: "ready"; unit: AiActUnitDetail }
  | { status: "error"; message: string };

function UnitBody({ citation }: { citation: Citation }) {
  const cached = unitCache.get(citation.unit_id);
  const [state, setState] = useState<UnitState>(
    cached ? { status: "ready", unit: cached } : { status: "loading" },
  );
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (unitCache.has(citation.unit_id)) return;
    const controller = new AbortController();
    api
      .get<AiActUnitDetail>(`/api/ai-act/units/${encodeURIComponent(citation.unit_id)}`, {
        signal: controller.signal,
      })
      .then((unit) => {
        unitCache.set(citation.unit_id, unit);
        setState({ status: "ready", unit });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ status: "error", message: failureMessage(error) });
      });
    return () => controller.abort();
  }, [citation.unit_id, attempt]);

  const unit = state.status === "ready" ? state.unit : null;
  const text = unit ? unitText(unit) : "";
  const ranges = unit ? quoteRanges(text, citation.quote) : [];

  return (
    <>
      <SheetHeader className="gap-1 border-b border-line p-5 pr-14">
        <SheetTitle className="text-lg leading-snug font-medium">{unit?.label || citation.label}</SheetTitle>
        <SheetDescription className={unit?.title ? "text-[0.875rem]" : "sr-only"}>
          {unit?.title || "The text of the provision"}
        </SheetDescription>
        {citation.quote ? (
          <p
            className={
              "mt-2 flex items-start gap-2 text-[0.8125rem] " +
              (citation.verified ? "text-ok" : "text-warn")
            }
          >
            {citation.verified ? (
              <Check aria-hidden className="mt-0.5 size-4 shrink-0" strokeWidth={2} />
            ) : (
              <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0" strokeWidth={1.75} />
            )}
            {citation.verified
              ? "The quoted words were found in this text."
              : "The quoted words were not found word for word in this text."}
          </p>
        ) : (
          <p className="mt-2 text-[0.8125rem] text-muted-foreground">
            Cited by its number; no words were quoted.
          </p>
        )}
      </SheetHeader>

      <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pb-8">
        {state.status === "loading" && (
          <div className="space-y-2.5" aria-label="Loading the text">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-11/12" />
            <Skeleton className="h-4 w-4/5" />
          </div>
        )}

        {state.status === "error" && (
          <div className="space-y-4">
            <blockquote className="border-l-2 border-line pl-3 text-[0.9375rem] leading-relaxed text-fg/85">
              {citation.quote}
            </blockquote>
            <p role="alert" className="text-[0.875rem] text-danger">
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

        {unit && (
          <>
            {ranges.length === 0 && citation.quote && (
              <blockquote className="mb-5 border-l-2 border-warn/60 pl-3 text-[0.9375rem] leading-relaxed text-fg/85">
                {citation.quote}
              </blockquote>
            )}
            <MarkedText text={text} ranges={ranges} />
          </>
        )}
      </div>
    </>
  );
}

/** The text of one provision, opened from a citation chip, with the quoted words marked. */
export function UnitSheet({
  citation,
  onClose,
}: {
  citation: Citation | null;
  onClose: () => void;
}) {
  const wide = useIsWide();
  // Keeps the last citation on screen while the sheet slides out.
  const [shown, setShown] = useState<Citation | null>(citation);
  if (citation && citation !== shown) setShown(citation);

  return (
    <Sheet open={citation !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        side={wide ? "right" : "bottom"}
        showCloseButton={false}
        className="gap-4 data-[side=bottom]:max-h-[85dvh] data-[side=bottom]:rounded-t-xl data-[side=right]:w-full data-[side=right]:sm:max-w-lg"
      >
        {shown && <UnitBody key={shown.unit_id + shown.quote} citation={shown} />}
        <SheetClose asChild>
          <Button variant="ghost" size="icon" className="absolute top-2.5 right-2.5" aria-label="Close">
            <X aria-hidden />
          </Button>
        </SheetClose>
      </SheetContent>
    </Sheet>
  );
}
