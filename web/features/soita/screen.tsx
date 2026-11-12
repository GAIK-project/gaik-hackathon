"use client";

import { Suspense, useCallback } from "react";
import { usePathname, useSearchParams } from "next/navigation";

import { Board } from "./board";
import { CallStage } from "./call-stage";
import { useReportTypes } from "./data";
import { ReportSheet } from "./report-view";

const PARAM = "raportti";

/**
 * Soita raportti: the call (one orb, the report filling in beside it) and under it the
 * board every report lands on. An open report is `?raportti=<id>`, so a link to it works
 * in the static export.
 */
export function SoitaScreen() {
  return (
    <Suspense fallback={null}>
      <Soita />
    </Suspense>
  );
}

function Soita() {
  const pathname = usePathname();
  const params = useSearchParams();
  const selectedId = params.get(PARAM);
  const types = useReportTypes();

  const open = useCallback(
    (id: string | null) => {
      const next = new URLSearchParams(params.toString());
      if (id) next.set(PARAM, id);
      else next.delete(PARAM);
      const query = next.toString();
      const url = query ? `${pathname}?${query}` : pathname;
      // The History API keeps useSearchParams in step without a round trip to the server,
      // so the sheet opens at once; Back closes an opened report.
      if (id) window.history.pushState(null, "", url);
      else window.history.replaceState(null, "", url);
    },
    [params, pathname],
  );
  const close = useCallback(() => open(null), [open]);

  return (
    <>
      <CallStage types={types} onOpenReport={open} />
      <Board selectedId={selectedId} onOpen={open} />
      <ReportSheet id={selectedId} onClose={close} />
    </>
  );
}
