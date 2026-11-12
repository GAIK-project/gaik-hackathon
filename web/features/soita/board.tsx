"use client";

import { useRef, useState } from "react";
import { AudioLines, LoaderCircle, Phone, Type, Upload } from "lucide-react";
import { cn } from "cn";

import { StackLogo, StatusWord } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { Channel, IntakeResult, ReportSummary } from "@/lib/types";

import { explain, useReports, useWhatsApp } from "./data";
import { CHANNEL_NAMES, statusWord, urgencyWord, WHATSAPP_NUMBER, when } from "./labels";

function ChannelIcon({ channel }: { channel: Channel }) {
  const name = CHANNEL_NAMES[channel] ?? channel;
  const box = "grid size-6 shrink-0 place-items-center text-muted-foreground";
  if (channel === "whatsapp" || channel === "mcp") {
    return (
      <span className={box} title={name}>
        <StackLogo id={channel} size={18} label={name} />
      </span>
    );
  }
  const Icon = channel === "call" ? Phone : channel === "upload" ? AudioLines : Type;
  return (
    <span className={box} title={name}>
      <Icon aria-label={name} className="size-4.5" strokeWidth={1.75} />
    </span>
  );
}

export function Board({
  selectedId,
  onOpen,
}: {
  selectedId: string | null;
  onOpen: (id: string) => void;
}) {
  const reports = useReports();
  const whatsapp = useWhatsApp();
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const { upsert } = reports;

  async function upload(file: File) {
    setUploading(true);
    setUploadError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const result = await api.postForm<IntakeResult>("/api/intake/audio", form, {
        query: { wait: false },
      });
      upsert(result.report);
      onOpen(result.report.id);
    } catch (error) {
      setUploadError(explain(error));
    } finally {
      setUploading(false);
    }
  }

  const rows = reports.data;

  return (
    <section className="mt-14 sm:mt-20" aria-labelledby="board-title">
      <div className="flex items-center justify-between gap-4">
        <h2 id="board-title" className="text-[1.5rem] font-light tracking-[-0.01em]">
          Reports
        </h2>
        <Button variant="secondary" disabled={uploading} onClick={() => fileRef.current?.click()}>
          {uploading ? (
            <LoaderCircle aria-hidden className="animate-spin" />
          ) : (
            <Upload aria-hidden strokeWidth={1.75} />
          )}
          {uploading ? "Uploading" : "Upload audio"}
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept="audio/*,.ogg,.webm,.mp3,.wav,.m4a"
          className="sr-only"
          tabIndex={-1}
          aria-hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) void upload(file);
          }}
        />
      </div>

      {whatsapp && (
        <p className="mt-3 flex items-center gap-2 text-[0.9375rem] text-muted-foreground">
          <StackLogo id="whatsapp" size={18} />
          <span>
            Or send a voice note on WhatsApp to{" "}
            <span className="whitespace-nowrap text-fg">{WHATSAPP_NUMBER}</span>
          </span>
        </p>
      )}
      {uploadError && (
        <p role="alert" className="mt-3 text-[0.875rem] text-danger">
          The audio file was not accepted. {uploadError}
        </p>
      )}

      <div className="mt-5">
        {rows === null && reports.loading ? (
          <div className="space-y-2">
            {[0, 1, 2].map((row) => (
              <Skeleton key={row} className="h-[4.25rem] w-full rounded-xl" />
            ))}
          </div>
        ) : rows === null ? (
          <div className="rounded-xl bg-surface p-5">
            <p className="text-muted-foreground">The board did not load. {reports.error}</p>
            <Button variant="secondary" className="mt-3" onClick={reports.reload}>
              Try again
            </Button>
          </div>
        ) : rows.length === 0 ? (
          <p className="rounded-xl bg-surface p-5 text-muted-foreground">
            No reports yet. Call the agent or upload an audio file.
          </p>
        ) : (
          <ul className="divide-y divide-line/60 overflow-hidden rounded-xl bg-surface">
            {rows.map((report) => (
              <Row
                key={report.id}
                report={report}
                fresh={reports.fresh.has(report.id)}
                selected={report.id === selectedId}
                onOpen={onOpen}
              />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function Row({
  report,
  fresh,
  selected,
  onOpen,
}: {
  report: ReportSummary;
  fresh: boolean;
  selected: boolean;
  onOpen: (id: string) => void;
}) {
  const status = statusWord(report.status);
  const urgency = report.urgency ? urgencyWord(report.urgency.label) : null;
  const time = when(report.created_at);
  const line = report.headline || report.caller_label;

  return (
    <li className={cn(fresh && "animate-rise")}>
      <button
        type="button"
        onClick={() => onOpen(report.id)}
        aria-current={selected || undefined}
        className={cn(
          "press flex min-h-[4.25rem] w-full cursor-pointer items-center gap-3 px-4 py-3 text-left outline-none",
          "transition-[background-color,transform] duration-150 ease-stage hover:bg-surface-2 focus-visible:bg-surface-2 aria-[current]:bg-surface-2",
        )}
      >
        <span className="hidden w-[5.5rem] shrink-0 text-[0.8125rem] text-muted-foreground tabular-nums sm:block">
          {time}
        </span>
        <ChannelIcon channel={report.channel} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[0.9375rem]">{report.type_title}</span>
          <span className="block truncate text-[0.8125rem] text-muted-foreground">
            <span className="tabular-nums sm:hidden">{time} </span>
            {urgency?.tone === "danger" && <span className="text-danger sm:hidden">{urgency.word} </span>}
            {line}
          </span>
        </span>
        {urgency && (
          <span
            className={cn(
              "hidden w-[5.5rem] shrink-0 text-right text-[0.8125rem] sm:block",
              urgency.tone === "danger" ? "text-danger" : "text-muted-foreground",
            )}
          >
            {urgency.word}
          </span>
        )}
        <span
          className="w-9 shrink-0 text-right text-[0.8125rem] text-muted-foreground tabular-nums"
          aria-label={`${report.filled} of ${report.total} fields`}
        >
          {report.filled}/{report.total}
        </span>
        <StatusWord tone={status.tone} className="w-[5.25rem] shrink-0 justify-end text-[0.875rem]">
          {status.word}
        </StatusWord>
      </button>
    </li>
  );
}
