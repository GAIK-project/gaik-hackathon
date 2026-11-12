"use client";

import { createElement, useEffect, useState } from "react";
import {
  ArrowUp,
  Check,
  ChevronDown,
  ClipboardList,
  HardHat,
  Keyboard,
  ShieldAlert,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { cn } from "cn";

import { StatusWord, useDock } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import type { CallLanguage, ReportType } from "@/lib/types";

import type { Loadable } from "./data";
import { callWord, LANGUAGES, PRIVACY_NOTICE } from "./labels";
import { Orb } from "./orb";
import { useCall } from "./use-call";

const TYPE_ICONS: Record<string, LucideIcon> = {
  shieldalert: ShieldAlert,
  hardhat: HardHat,
  wrench: Wrench,
  clipboardlist: ClipboardList,
};

function TypeIcon({ name, className }: { name: string | undefined; className?: string }) {
  const key = (name ?? "").toLowerCase().replace(/[^a-z]/g, "");
  return createElement(TYPE_ICONS[key] ?? ClipboardList, {
    "aria-hidden": true,
    strokeWidth: 1.75,
    className,
  });
}

export function CallStage({
  types,
  onOpenReport,
}: {
  types: Loadable<ReportType[]>;
  onOpenReport: (id: string) => void;
}) {
  const call = useCall();
  const { setHidden } = useDock();
  const [typeId, setTypeId] = useState<string | null>(null);
  const [language, setLanguage] = useState<CallLanguage>("en");
  const [typing, setTyping] = useState(false);
  const [draft, setDraft] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);

  const list = types.data ?? [];
  const selected = list.find((type) => type.id === typeId) ?? list[0] ?? null;
  const shown = call.reportType ?? selected;
  const { active } = call;

  useEffect(() => {
    setHidden(active);
    return () => setHidden(false);
  }, [active, setHidden]);

  // Without a microphone the typed turns are the way to talk: open the field.
  const noMic = active && !call.mic && call.notice !== null;
  const showInput = typing || noMic;

  function pressOrb() {
    if (active) call.stop();
    else if (selected) call.start({ typeId: selected.id, language, mic: true });
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || !selected) return;
    if (active) call.sendText(text);
    else call.start({ typeId: selected.id, language, mic: false, text });
    setDraft("");
  }

  const word = callWord(call.phase, call.state);
  const caption =
    call.caption?.text ??
    (call.phase === "idle"
      ? "Press the orb and tell what happened."
      : call.phase === "saved"
        ? "The report is on the board."
        : "");

  return (
    <section className="grid items-center gap-x-12 gap-y-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <div className="flex flex-col items-center text-center">
        <Orb
          active={active}
          busy={call.phase === "connecting" || call.phase === "saving"}
          level={call.level}
          onPress={pressOrb}
          label={active ? "End the call" : "Start the call"}
          className={cn(!selected && "pointer-events-none opacity-60")}
        />
        <StatusWord
          className="-mt-2 text-[1.375rem] font-light"
          tone={call.phase === "saved" ? "ok" : "default"}
        >
          {word}
        </StatusWord>
        <p
          className={cn(
            "mt-1.5 line-clamp-2 min-h-[3.1em] max-w-[34ch] text-[0.9375rem] text-balance",
            call.caption?.role === "agent" ? "text-fg/85" : "text-muted-foreground",
          )}
        >
          {caption}
        </p>
        {call.notice && (
          <p role="alert" className="mb-2 max-w-[38ch] text-[0.875rem] text-warn">
            {call.notice}
          </p>
        )}

        <div
          className={cn("mt-2 flex flex-wrap items-center justify-center gap-2", active && "hidden")}
        >
          {types.loading && !types.data ? (
            <Skeleton className="h-11 w-52 rounded-lg" />
          ) : types.error && !types.data ? (
            <p className="text-[0.875rem] text-muted-foreground">
              Report types did not load. {types.error}{" "}
              <button
                type="button"
                onClick={types.reload}
                className="press cursor-pointer text-accent underline-offset-4 hover:underline"
              >
                Try again
              </button>
            </p>
          ) : (
            selected && (
              <>
                <Popover open={pickerOpen} onOpenChange={setPickerOpen}>
                  <PopoverTrigger asChild>
                    <Button variant="secondary" className="max-w-[16rem]">
                      <TypeIcon name={selected.icon} />
                      <span className="truncate">{selected.title}</span>
                      <ChevronDown aria-hidden className="text-muted-foreground" />
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent
                    align="center"
                    collisionPadding={12}
                    className="w-[min(22rem,calc(100vw-1.5rem))] gap-0 p-1.5 ring-line"
                  >
                    <ul>
                      {list.map((type) => {
                        const on = type.id === selected.id;
                        return (
                          <li key={type.id}>
                            <button
                              type="button"
                              onClick={() => {
                                setTypeId(type.id);
                                setPickerOpen(false);
                              }}
                              className="press flex w-full cursor-pointer items-start gap-3 rounded-lg px-3 py-2.5 text-left outline-none hover:bg-line/50 focus-visible:bg-line/50"
                            >
                              <TypeIcon name={type.icon} className="mt-0.5 size-4.5 shrink-0 text-muted-foreground" />
                              <span className="min-w-0 flex-1">
                                <span className="block text-[0.9375rem]">{type.title}</span>
                                <span className="line-clamp-2 block text-[0.8125rem] text-muted-foreground">
                                  {type.description}
                                </span>
                              </span>
                              {on && <Check aria-hidden className="mt-0.5 size-4 shrink-0 text-accent" />}
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  </PopoverContent>
                </Popover>

                <div role="radiogroup" aria-label="Language of the call" className="flex rounded-lg bg-surface-2">
                  {LANGUAGES.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      role="radio"
                      aria-checked={language === item.id}
                      aria-label={item.name}
                      title={item.name}
                     
                      onClick={() => setLanguage(item.id)}
                      className={cn(
                        "press h-11 min-w-11 cursor-pointer rounded-lg px-3 text-[0.8125rem] font-medium outline-none transition-[background-color,color,transform] duration-150 ease-stage focus-visible:ring-2 focus-visible:ring-accent",
                        language === item.id ? "bg-line text-fg" : "text-muted-foreground hover:text-fg",
                      )}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </>
            )
          )}
        </div>

        {showInput ? (
          <form onSubmit={submit} className="mt-3 flex w-full max-w-sm animate-rise items-center gap-2">
            <Input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder={active ? "Type your answer" : "Type what happened"}
              aria-label="Your turn as text"
              autoComplete="off"
              enterKeyHint="send"
              disabled={!selected || call.phase === "saving"}
              className="h-11"
            />
            <Button
              type="submit"
              size="icon"
              aria-label="Send"
              title="Send"
              disabled={!draft.trim() || !selected || call.phase === "saving"}
            >
              <ArrowUp aria-hidden />
            </Button>
          </form>
        ) : (
          <button
            type="button"
            onClick={() => setTyping(true)}
            className="press mt-3 inline-flex h-11 cursor-pointer items-center gap-2 rounded-lg px-3 text-[0.875rem] text-muted-foreground outline-none transition-[color,transform] duration-150 ease-stage hover:text-fg focus-visible:ring-2 focus-visible:ring-accent"
          >
            <Keyboard aria-hidden className="size-4" strokeWidth={1.75} />
            Type instead
          </button>
        )}
        <p className="mt-1 text-[0.8125rem] text-muted-foreground">{PRIVACY_NOTICE}</p>
      </div>

      <LiveFields
        type={shown}
        loading={types.loading && !types.data}
        values={call.values}
        asking={call.asking}
        filled={call.filled}
        started={call.phase !== "idle"}
        savedId={call.phase === "saved" ? call.reportId : null}
        onOpenReport={onOpenReport}
      />
    </section>
  );
}

function LiveFields({
  type,
  loading,
  values,
  asking,
  filled,
  started,
  savedId,
  onOpenReport,
}: {
  type: ReportType | null;
  loading: boolean;
  values: Record<string, string>;
  asking: string | null;
  filled: number;
  started: boolean;
  savedId: string | null;
  onOpenReport: (id: string) => void;
}) {
  if (loading) {
    return (
      <div className="rounded-xl bg-surface p-5 sm:p-6">
        <Skeleton className="h-6 w-48" />
        <div className="mt-6 space-y-4">
          {[0, 1, 2, 3, 4].map((row) => (
            <Skeleton key={row} className="h-5 w-full" />
          ))}
        </div>
      </div>
    );
  }
  if (!type) return null;

  const required = type.fields.filter((field) => field.required).length;

  return (
    <div className="rounded-xl bg-surface p-5 sm:p-6">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="text-[1.125rem] font-medium">{type.title}</h2>
        <p className="shrink-0 text-[0.875rem] text-muted-foreground tabular-nums" aria-label={`${filled} of ${required} required fields filled`}>
          {started ? `${filled}/${required}` : `${type.fields.length} fields`}
        </p>
      </div>
      <dl className="mt-4 divide-y divide-line/60">
        {type.fields.map((field) => {
          const value = values[field.name];
          const isAsking = asking === field.name && !value;
          return (
            <div
              key={field.name}
              className="grid gap-x-4 gap-y-0.5 py-2.5 sm:grid-cols-[minmax(0,11rem)_minmax(0,1fr)]"
            >
              <dt
                className={cn(
                  "flex items-center gap-2 text-[0.8125rem] transition-colors duration-200 ease-stage",
                  isAsking ? "text-accent" : "text-muted-foreground",
                )}
              >
                <span className="min-w-0">{field.label}</span>
                {isAsking && (
                  <span aria-label="being asked" className="size-1.5 shrink-0 animate-pulse rounded-full bg-accent" />
                )}
              </dt>
              <dd className="min-h-[1.55em] text-[0.9375rem]">
                {value && (
                  <span key={value} className="inline-block animate-rise">
                    {value}
                  </span>
                )}
              </dd>
            </div>
          );
        })}
      </dl>
      {savedId && (
        <Button variant="secondary" className="mt-4 animate-rise" onClick={() => onOpenReport(savedId)}>
          Open the report
        </Button>
      )}
    </div>
  );
}
