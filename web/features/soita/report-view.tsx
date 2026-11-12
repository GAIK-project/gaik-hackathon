"use client";

import { useState } from "react";
import { Check, CircleAlert, Download, LoaderCircle, Pencil, Quote, Send, X } from "lucide-react";
import { cn } from "cn";

import { StackLogo, StatusWord } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { api, apiUrl } from "@/lib/api";
import type { FieldValue, Report, TimelineEvent } from "@/lib/types";

import { explain, useRecipients, useReport } from "./data";
import { CHANNEL_NAMES, duration, fileSize, statusWord, urgencyWord, when } from "./labels";

export function ReportSheet({ id, onClose }: { id: string | null; onClose: () => void }) {
  return (
    <Sheet open={id !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        side="right"
        showCloseButton={false}
        aria-describedby={undefined}
        className="w-full! gap-0 overflow-y-auto overscroll-contain bg-surface p-0 [scrollbar-width:none] sm:max-w-xl!"
      >
        {id && <ReportBody key={id} id={id} onClose={onClose} />}
      </SheetContent>
    </Sheet>
  );
}

function ReportBody({ id, onClose }: { id: string; onClose: () => void }) {
  const loaded = useReport(id);
  const report = loaded.data;

  return (
    <div className="flex min-h-full flex-col px-5 pt-5 pb-10 sm:px-7 sm:pt-7">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <SheetTitle className="text-[1.5rem] leading-tight font-light tracking-[-0.01em]">
            {report?.type_title ?? "Report"}
          </SheetTitle>
          {report?.headline && (
            <SheetDescription className="mt-1.5 text-[0.9375rem]">{report.headline}</SheetDescription>
          )}
        </div>
        <Button variant="ghost" size="icon" aria-label="Close the report" title="Close" onClick={onClose} className="-mt-1.5 -mr-2 shrink-0">
          <X aria-hidden />
        </Button>
      </div>

      {!report && loaded.loading && (
        <div className="mt-8 space-y-4">
          {[0, 1, 2, 3, 4].map((row) => (
            <Skeleton key={row} className="h-6 w-full" />
          ))}
        </div>
      )}
      {!report && !loaded.loading && (
        <div className="mt-8">
          <p className="text-muted-foreground">The report did not open. {loaded.error}</p>
          <Button variant="secondary" className="mt-3" onClick={loaded.reload}>
            Try again
          </Button>
        </div>
      )}
      {report && <ReportContent report={report} onChange={loaded.set} />}
    </div>
  );
}

function ReportContent({ report, onChange }: { report: Report; onChange: (report: Report) => void }) {
  const status = statusWord(report.status);
  const urgency = report.urgency ? urgencyWord(report.urgency.label) : null;
  const needsApproval = report.status === "needs_approval";
  const [editing, setEditing] = useState(false);
  const docs = report.files.filter((file) => file.kind === "docx");

  return (
    <>
      <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-1 text-[0.875rem] text-muted-foreground">
        <StatusWord tone={status.tone} className="font-medium">
          {status.word}
        </StatusWord>
        <span className="tabular-nums">{when(report.created_at)}</span>
        <span>{CHANNEL_NAMES[report.channel] ?? report.channel}</span>
        {urgency && (
          <span className={cn(urgency.tone === "danger" && "text-danger")}>
            {urgency.word}
            {report.urgency && ` ${Math.round(report.urgency.confidence * 100)} %`}
          </span>
        )}
      </div>

      {needsApproval && !editing && (
        <Approval report={report} onChange={onChange} onCorrect={() => setEditing(true)} />
      )}
      {report.status === "sent" && report.email?.to && (
        <p className="mt-5 flex items-center gap-2 text-[0.9375rem]">
          <Check aria-hidden className="size-4 shrink-0 text-ok" />
          <span className="min-w-0 break-words">
            Sent to {report.email.to}
            {report.email.sent_at && ` at ${when(report.email.sent_at)}`}
          </span>
        </p>
      )}

      {editing ? (
        <FieldEditor report={report} onChange={onChange} onDone={() => setEditing(false)} />
      ) : (
        <Fields fields={report.fields} />
      )}

      {docs.map((file) => (
        <a
          key={file.name}
          href={apiUrl(file.url)}
          download={file.name}
          className="press mt-6 flex min-h-14 items-center gap-3 rounded-lg bg-surface-2 px-4 py-2.5 outline-none transition-[background-color,transform] duration-150 ease-stage hover:bg-line/70 focus-visible:ring-2 focus-visible:ring-accent"
        >
          <StackLogo id="word" size={24} />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[0.9375rem]">{file.name}</span>
            <span className="block text-[0.8125rem] text-muted-foreground">Word file, {fileSize(file.size)}</span>
          </span>
          <Download aria-label="Download" className="size-4.5 shrink-0 text-muted-foreground" strokeWidth={1.75} />
        </a>
      ))}

      {report.timeline.length > 0 && (
        <section className="mt-9">
          <h3 className="text-[1.0625rem] font-medium">What the agent did</h3>
          <ol className="mt-4">
            {report.timeline.map((event, index) => (
              <Step key={event.id} event={event} last={index === report.timeline.length - 1} />
            ))}
          </ol>
        </section>
      )}
    </>
  );
}

function Fields({ fields }: { fields: FieldValue[] }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <dl className="mt-6 divide-y divide-line/60 border-y border-line/60">
      {fields.map((field) => {
        const expanded = open === field.name;
        const body = (
          <>
            <dt className="text-[0.8125rem] text-muted-foreground">{field.label}</dt>
            <dd className="mt-0.5 flex items-start gap-2 text-[0.9375rem]">
              <span className={cn("min-w-0 flex-1 break-words", !field.value && "text-muted-foreground")}>
                {field.value ?? "Not given"}
              </span>
              {field.evidence && (
                <Quote
                  aria-hidden
                  strokeWidth={1.75}
                  className={cn(
                    "mt-1 size-3.5 shrink-0 transition-colors duration-150",
                    expanded ? "text-accent" : "text-muted-foreground",
                  )}
                />
              )}
            </dd>
            {expanded && field.evidence && (
              <dd className="mt-2 animate-rise border-l-2 border-accent/60 pl-3 text-[0.875rem] text-muted-foreground">
                <span className="sr-only">Evidence in the transcript: </span>“{field.evidence}”
              </dd>
            )}
          </>
        );
        return field.evidence ? (
          <button
            key={field.name}
            type="button"
            aria-expanded={expanded}
            onClick={() => setOpen(expanded ? null : field.name)}
            className="press block w-full cursor-pointer py-3 text-left outline-none focus-visible:bg-surface-2"
          >
            {body}
          </button>
        ) : (
          <div key={field.name} className="py-3">
            {body}
          </div>
        );
      })}
    </dl>
  );
}

function FieldEditor({
  report,
  onChange,
  onDone,
}: {
  report: Report;
  onChange: (report: Report) => void;
  onDone: () => void;
}) {
  const [draft, setDraft] = useState<Record<string, string>>(() =>
    Object.fromEntries(report.fields.map((field) => [field.name, field.value ?? ""])),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const values: Record<string, string | null> = {};
    for (const field of report.fields) {
      const next = draft[field.name]?.trim() ?? "";
      if (next !== (field.value ?? "")) values[field.name] = next || null;
    }
    if (Object.keys(values).length === 0) {
      onDone();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      onChange(await api.patch<Report>(`/api/reports/${encodeURIComponent(report.id)}/fields`, { values }));
      onDone();
    } catch (e) {
      setError(explain(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={save} className="mt-6 space-y-4">
      {report.fields.map((field) => {
        const value = draft[field.name] ?? "";
        const long = value.length > 60 || (field.value?.length ?? 0) > 60;
        const props = {
          id: `field-${field.name}`,
          value,
          disabled: saving,
          onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
            setDraft((current) => ({ ...current, [field.name]: event.target.value })),
        };
        return (
          <div key={field.name}>
            <label htmlFor={props.id} className="text-[0.8125rem] text-muted-foreground">
              {field.label}
            </label>
            {long ? (
              <Textarea {...props} rows={3} className="mt-1 text-[0.9375rem]" />
            ) : (
              <Input {...props} autoComplete="off" className="mt-1 h-11 text-[0.9375rem]" />
            )}
          </div>
        );
      })}
      {error && (
        <p role="alert" className="text-[0.875rem] text-danger">
          The changes were not saved. {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" disabled={saving}>
          {saving && <LoaderCircle aria-hidden className="animate-spin" />}
          {saving ? "Saving" : "Save changes"}
        </Button>
        <Button type="button" variant="ghost" disabled={saving} onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function Approval({
  report,
  onChange,
  onCorrect,
}: {
  report: Report;
  onChange: (report: Report) => void;
  onCorrect: () => void;
}) {
  const recipients = useRecipients(true);
  const [chosen, setChosen] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const info = recipients.data;
  const options = info?.recipients ?? [];
  const to = chosen ?? info?.default ?? report.email?.to ?? options[0] ?? null;
  const mailOff = info !== null && (!info.enabled || options.length === 0);

  async function approve() {
    setSending(true);
    setError(null);
    try {
      onChange(
        await api.post<Report>(`/api/reports/${encodeURIComponent(report.id)}/approve`, to ? { to } : {}),
      );
    } catch (e) {
      setError(explain(e));
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="mt-5 rounded-lg bg-surface-2 p-4">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.9375rem]">
        <StackLogo id="gmail" size={16} />
        {recipients.loading && !info ? (
          <span className="text-muted-foreground">Looking up the recipient</span>
        ) : mailOff ? (
          <span className="text-muted-foreground">Email is switched off on this server.</span>
        ) : recipients.error && !info ? (
          <span className="text-muted-foreground">Goes to the default recipient.</span>
        ) : options.length > 1 ? (
          <>
            <label htmlFor="approve-to" className="text-muted-foreground">
              Send to
            </label>
            <select
              id="approve-to"
              value={to ?? ""}
              disabled={sending}
              onChange={(event) => setChosen(event.target.value)}
              className="h-11 max-w-full min-w-0 flex-1 cursor-pointer rounded-lg border border-line bg-surface px-3 text-[0.9375rem] outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              {options.map((address) => (
                <option key={address} value={address}>
                  {address}
                </option>
              ))}
            </select>
          </>
        ) : (
          <span className="min-w-0 break-all">
            <span className="text-muted-foreground">Send to </span>
            {to}
          </span>
        )}
      </div>
      {error && (
        <p role="alert" className="mt-3 flex items-start gap-2 text-[0.875rem] text-danger">
          <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
          <span>The report was not sent. {error}</span>
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button onClick={approve} disabled={sending || mailOff}>
          {sending ? <LoaderCircle aria-hidden className="animate-spin" /> : <Send aria-hidden strokeWidth={1.75} />}
          {sending ? "Sending" : "Approve and send"}
        </Button>
        <Button variant="secondary" onClick={onCorrect} disabled={sending}>
          <Pencil aria-hidden strokeWidth={1.75} />
          Correct
        </Button>
      </div>
    </div>
  );
}

const DOT: Record<TimelineEvent["status"], string> = {
  running: "bg-accent animate-pulse",
  done: "bg-ok",
  waiting: "bg-warn",
  error: "bg-danger",
};

function Step({ event, last }: { event: TimelineEvent; last: boolean }) {
  return (
    <li className="relative flex gap-3.5 pb-5 last:pb-0">
      {!last && <span aria-hidden className="absolute top-3 bottom-0 left-[0.3125rem] w-px bg-line" />}
      <span aria-hidden className={cn("relative mt-[0.4375rem] size-[0.6875rem] shrink-0 rounded-full", DOT[event.status])} />
      <div className="min-w-0 flex-1">
        <p className="flex items-baseline justify-between gap-3 text-[0.9375rem]">
          <span className={cn("min-w-0", event.status === "error" && "text-danger")}>{event.label}</span>
          {event.ms !== null && (
            <span className="shrink-0 text-[0.8125rem] text-muted-foreground tabular-nums">{duration(event.ms)}</span>
          )}
        </p>
        {event.detail && <p className="text-[0.875rem] break-words text-muted-foreground">{event.detail}</p>}
        {event.component && <p className="mt-0.5 font-mono text-[0.75rem] text-muted-foreground">{event.component}</p>}
      </div>
    </li>
  );
}
