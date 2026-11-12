"use client";

import { Download } from "lucide-react";

import { SourceChip, StackLogo } from "@/components/shell";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiUrl } from "@/lib/api";

import { percent } from "./text";
import type { RunResult } from "./types";

type Of<K extends RunResult["kind"]> = Extract<RunResult, { kind: K }>;

const empty = <span className="text-muted-foreground">Not found</span>;

function TableResult({ result }: { result: Of<"table"> }) {
  if (result.rows.length === 0) {
    return <p className="text-muted-foreground">The documents gave no rows.</p>;
  }
  return (
    <div className="no-scrollbar -mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
      <Table className="text-[0.875rem]">
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-9 pl-0 font-normal text-muted-foreground">Source</TableHead>
            {result.columns.map((column) => (
              <TableHead key={column.name} className="h-9 font-normal text-muted-foreground">
                {column.label}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {result.rows.map((row, index) => (
            <TableRow key={`${row.source}-${index}`} className="hover:bg-transparent">
              <TableCell className="max-w-44 truncate pl-0 text-muted-foreground" title={row.source}>
                {row.source}
              </TableCell>
              {result.columns.map((column) => (
                <TableCell
                  key={column.name}
                  className={
                    (row.values[column.name]?.length ?? 0) > 24
                      ? "max-w-64 min-w-40 whitespace-normal"
                      : "whitespace-nowrap"
                  }
                >
                  {row.values[column.name] ?? empty}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function ReportResult({ result }: { result: Of<"report"> }) {
  return (
    <div className="flex flex-col gap-5">
      <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
        {result.fields.map((field) => (
          <div key={field.name} className="min-w-0">
            <dt className="text-[0.8125rem] text-muted-foreground">{field.label}</dt>
            <dd className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="min-w-0">{field.value ?? empty}</span>
              {field.value && field.evidence && (
                <SourceChip label="Source" title={`${field.label}: words in the recording`}>
                  <blockquote>{field.evidence}</blockquote>
                </SourceChip>
              )}
            </dd>
          </div>
        ))}
      </dl>
      {result.transcript && (
        <div>
          <h4 className="text-[0.8125rem] text-muted-foreground">Transcript</h4>
          <p
            tabIndex={0}
            className="no-scrollbar mt-1.5 max-h-48 overflow-y-auto rounded-lg bg-bg/60 px-3.5 py-3 text-[0.875rem] leading-relaxed whitespace-pre-wrap"
          >
            {result.transcript}
          </p>
        </div>
      )}
      {result.file && (
        <Button asChild variant="secondary" className="self-start">
          <a href={apiUrl(result.file.url)} download={result.file.name}>
            <StackLogo id="word" size={18} />
            Download Word report
            <Download aria-hidden className="text-muted-foreground" />
          </a>
        </Button>
      )}
    </div>
  );
}

function AnswerResult({ result }: { result: Of<"answer"> }) {
  return (
    <div className="flex flex-col gap-3">
      <p className="text-[0.875rem] text-muted-foreground">{result.question}</p>
      {result.found ? (
        <>
          <p className="max-w-[68ch] text-[1.0625rem] leading-relaxed whitespace-pre-wrap">
            {result.answer}
          </p>
          {result.sources.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {result.sources.map((source, index) => {
                const label =
                  source.page === null ? source.title : `${source.title}, p. ${source.page}`;
                return (
                  <SourceChip key={`${label}-${index}`} label={label}>
                    <blockquote>{source.quote}</blockquote>
                  </SourceChip>
                );
              })}
            </div>
          )}
        </>
      ) : (
        <p className="text-[1.0625rem]">The documents do not answer this.</p>
      )}
    </div>
  );
}

function LabelsResult({ result }: { result: Of<"labels"> }) {
  if (result.rows.length === 0) {
    return <p className="text-muted-foreground">There was nothing to label.</p>;
  }
  return (
    <ul className="flex flex-col divide-y divide-line">
      {result.rows.map((row, index) => (
        <li
          key={`${row.source}-${index}`}
          className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 gap-y-1.5 py-3 first:pt-0 last:pb-0"
        >
          <span className="truncate text-[0.875rem] text-muted-foreground" title={row.source}>
            {row.source}
          </span>
          <span className="text-[0.875rem] tabular-nums text-muted-foreground">
            {percent(row.confidence)}
          </span>
          <span className="font-medium">{row.label}</span>
          <span
            role="meter"
            aria-label="Confidence"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(row.confidence * 100)}
            className="h-1.5 w-24 self-center overflow-hidden rounded-full bg-surface-2 sm:w-40"
          >
            <span
              className="block h-full origin-left rounded-full bg-accent transition-transform duration-300 ease-stage"
              style={{ transform: `scaleX(${Math.min(1, Math.max(0, row.confidence))})` }}
            />
          </span>
        </li>
      ))}
    </ul>
  );
}

export function ResultView({ result }: { result: RunResult }) {
  switch (result.kind) {
    case "table":
      return <TableResult result={result} />;
    case "report":
      return <ReportResult result={result} />;
    case "answer":
      return <AnswerResult result={result} />;
    case "labels":
      return <LabelsResult result={result} />;
  }
}
