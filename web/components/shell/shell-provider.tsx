"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import { usePathname } from "next/navigation";
import { MotionConfig } from "motion/react";

import { TooltipProvider } from "@/components/ui/tooltip";

export interface DockApi {
  /** The dock is hidden on the current page. */
  hidden: boolean;
  /**
   * Hide or show the dock, for example while a call is live. It applies to the current
   * page only: leaving the page brings the dock back even if nobody called setHidden(false).
   */
  setHidden: (hidden: boolean) => void;
  /** The "How it's made" panel is open. */
  howOpen: boolean;
  setHowOpen: (open: boolean) => void;
  toggleHow: () => void;
}

const DockContext = createContext<DockApi | null>(null);

/** Route path without a trailing slash ("/" stays "/"). */
export function normalizePath(pathname: string | null): string {
  if (!pathname) return "/";
  const trimmed = pathname.replace(/\/+$/, "");
  return trimmed === "" ? "/" : trimmed;
}

export function ShellProvider({ children }: { children: React.ReactNode }) {
  const path = normalizePath(usePathname());
  // Remembering the page that asked for it, not a bare boolean, resets the dock on navigation.
  const [hiddenOn, setHiddenOn] = useState<string | null>(null);
  const [howOpen, setHowOpen] = useState(false);

  const setHidden = useCallback(
    (hidden: boolean) => setHiddenOn(hidden ? path : null),
    [path],
  );
  const toggleHow = useCallback(() => setHowOpen((open) => !open), []);

  const value = useMemo<DockApi>(
    () => ({ hidden: hiddenOn === path, setHidden, howOpen, setHowOpen, toggleHow }),
    [hiddenOn, path, setHidden, howOpen, toggleHow],
  );

  return (
    <DockContext.Provider value={value}>
      {/* "user": with reduced motion Motion skips transforms and shows the end state. */}
      <MotionConfig reducedMotion="user">
        <TooltipProvider delayDuration={300}>{children}</TooltipProvider>
      </MotionConfig>
    </DockContext.Provider>
  );
}

/** Dock and "How it's made" state. Use inside the root layout (every page is). */
export function useDock(): DockApi {
  const api = useContext(DockContext);
  if (!api) throw new Error("useDock must be used inside <ShellProvider>");
  return api;
}
