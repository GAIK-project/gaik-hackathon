"use client";

import { AnimatePresence, motion } from "motion/react";
import { cn } from "cn";

const tones = {
  default: "text-fg",
  muted: "text-muted-foreground",
  accent: "text-accent",
  ok: "text-ok",
  warn: "text-warn",
  danger: "text-danger",
} as const;

export type StatusTone = keyof typeof tones;

/**
 * One status word ("Listening", "Saving", "Sent"). When the word changes, the new
 * one rises into place while the old one leaves upward. One line of status instead of a
 * paragraph; screen readers hear the change. Colour is for state only (`tone`).
 */
export function StatusWord({
  children,
  tone = "default",
  className,
}: {
  children: string;
  tone?: StatusTone;
  className?: string;
}) {
  return (
    <span
      role="status"
      aria-live="polite"
      className={cn("relative inline-flex overflow-hidden align-bottom", className)}
    >
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span
          key={children}
          initial={{ opacity: 0, y: "0.6em" }}
          animate={{ opacity: 1, y: 0, transition: { duration: 0.18, ease: [0.16, 1, 0.3, 1] } }}
          exit={{ opacity: 0, y: "-0.5em", transition: { duration: 0.12, ease: [0.4, 0, 1, 1] } }}
          className={cn("inline-block whitespace-nowrap", tones[tone])}
        >
          {children}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}
