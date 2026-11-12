"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { subscribe } from "@/lib/sse";
import type { Report, ReportSummary, ReportType } from "@/lib/types";

import type { MailRecipientsInfo, WhatsAppStatus } from "./types";

const GENERIC: Record<string, string> = {
  network: "No connection to the server. Check the network and try again.",
  unauthorized: "Your session has expired. Reload the page.",
  not_found: "This is not available on the server yet.",
  too_large: "The file is too large.",
  rate_limited: "Too many requests. Wait a moment and try again.",
  server_error: "The service failed. Try again in a moment.",
  stream_interrupted: "The connection dropped. Try again.",
  unknown: "The request failed. Try again.",
};

/** An English sentence for anything a call threw; the server's own message when it sent one. */
export function explain(error: unknown): string {
  if (error instanceof ApiError) return GENERIC[error.code] ?? error.message;
  return GENERIC.unknown;
}

export interface Loadable<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

function useLoad<T>(path: string | null): Loadable<T> & { set: (value: T) => void } {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({
    data: null,
    error: null,
    loading: path !== null,
  });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (path === null) return;
    let cancelled = false;
    api
      .get<T>(path)
      .then((data) => {
        if (!cancelled) setState({ data, error: null, loading: false });
      })
      .catch((error: unknown) => {
        if (!cancelled) setState((current) => ({ ...current, error: explain(error), loading: false }));
      });
    return () => {
      cancelled = true;
    };
  }, [path, attempt]);

  const reload = useCallback(() => {
    setState((current) => ({ ...current, error: null, loading: true }));
    setAttempt((value) => value + 1);
  }, []);
  const set = useCallback((data: T) => setState({ data, error: null, loading: false }), []);

  return { ...state, reload, set };
}

export function useReportTypes(): Loadable<ReportType[]> {
  return useLoad<ReportType[]>("/api/report-types");
}

export function useRecipients(enabled: boolean): Loadable<MailRecipientsInfo> {
  return useLoad<MailRecipientsInfo>(enabled ? "/api/mail/recipients" : null);
}

/** The board: the list once, then every change from the stream, merged in place by id. */
export function useReports(): Loadable<ReportSummary[]> & { fresh: ReadonlySet<string>; upsert: (r: ReportSummary) => void } {
  const [rows, setRows] = useState<ReportSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [fresh, setFresh] = useState<ReadonlySet<string>>(new Set());
  const loadedRef = useRef(false);

  const upsert = useCallback((report: ReportSummary) => {
    setRows((current) => {
      const list = current ?? [];
      const index = list.findIndex((row) => row.id === report.id);
      if (index === -1) {
        if (loadedRef.current) setFresh((ids) => new Set(ids).add(report.id));
        return [report, ...list];
      }
      const next = list.slice();
      next[index] = { ...list[index], ...report };
      return next;
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    function load() {
      api
        .get<ReportSummary[]>("/api/reports", { query: { limit: 50 } })
        .then((list) => {
          if (cancelled) return;
          loadedRef.current = true;
          setRows(list);
          setError(null);
          setLoading(false);
        })
        .catch((e: unknown) => {
          if (cancelled) return;
          setError(explain(e));
          setLoading(false);
        });
    }
    // Subscribe first, then read the list, so no change falls between the two.
    const stream = subscribe<{ report: ReportSummary }>("/api/reports/stream", {
      on: { report: (summary) => upsert(summary) },
      onOpen: load,
      onError: () => load(),
    });
    load();
    return () => {
      cancelled = true;
      stream.close();
    };
  }, [attempt, upsert]);

  const reload = useCallback(() => {
    setError(null);
    setLoading(true);
    setAttempt((value) => value + 1);
  }, []);

  return { data: rows, error, loading, reload, fresh, upsert };
}

/** One report, kept current by its own stream. */
export function useReport(id: string | null): Loadable<Report> & { set: (r: Report) => void } {
  const loaded = useLoad<Report>(id ? `/api/reports/${encodeURIComponent(id)}` : null);
  const { set } = loaded;

  useEffect(() => {
    if (!id) return;
    const stream = subscribe<{ report: Report }>(`/api/reports/${encodeURIComponent(id)}/stream`, {
      on: { report: (report) => set(report) },
    });
    return () => stream.close();
  }, [id, set]);

  return loaded;
}

export function useWhatsApp(): boolean {
  const [active, setActive] = useState(false);
  useEffect(() => {
    let cancelled = false;
    function check() {
      api
        .get<WhatsAppStatus>("/api/whatsapp/status")
        .then((status) => {
          if (!cancelled) setActive(Boolean(status.active));
        })
        .catch(() => {
          if (!cancelled) setActive(false);
        });
    }
    check();
    const timer = setInterval(check, 30000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);
  return active;
}
