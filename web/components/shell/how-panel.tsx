"use client";

import { useEffect, useRef } from "react";
import { AnimatePresence, motion, type Variants } from "motion/react";
import { X } from "lucide-react";

import { Funding } from "@/components/shell/funding";
import { useDock } from "@/components/shell/shell-provider";
import { StackLogo } from "@/components/shell/stack-logo";
import { stackEntry } from "@/lib/stack";
import type { How } from "@/lib/types";

/**
 * "How it's made": the pipeline of a page as a strip. Closed until the dock's toggle
 * opens it; then the steps appear once, left to right (top to bottom on a phone). Each step
 * shows what happens, one line of detail, the gaik component in mono and the logo of the
 * service behind it. Model and service names belong here and nowhere else in the interface.
 *
 * PageShell renders it after the working surface; the data is `features/<demo>/how.ts`.
 */

const EASE_STAGE = [0.16, 1, 0.3, 1] as const;
const EASE_EXIT = [0.4, 0, 1, 1] as const;

const panel: Variants = {
  closed: { opacity: 0, y: 12 },
  open: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.32, ease: EASE_STAGE, staggerChildren: 0.07, delayChildren: 0.12 },
  },
  exit: { opacity: 0, y: 8, transition: { duration: 0.15, ease: EASE_EXIT } },
};

const step: Variants = {
  closed: { opacity: 0, x: -12 },
  open: { opacity: 1, x: 0, transition: { duration: 0.36, ease: EASE_STAGE } },
};

const fade: Variants = {
  closed: { opacity: 0 },
  open: { opacity: 1, transition: { duration: 0.36, ease: EASE_STAGE } },
};

export function HowPanel({ how, funding = true }: { how: How; funding?: boolean }) {
  const { howOpen, setHowOpen } = useDock();
  const ref = useRef<HTMLElement>(null);
  const wasOpen = useRef(howOpen);

  // Bring the panel into view when the visitor opens it, not when a page arrives with it open.
  useEffect(() => {
    if (howOpen && !wasOpen.current) {
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      ref.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
    }
    wasOpen.current = howOpen;
  }, [howOpen]);

  const stack = how.stack.filter((id) => stackEntry(id));

  return (
    <AnimatePresence initial={false}>
      {howOpen && (
        <motion.section
          ref={ref}
          aria-labelledby="how-heading"
          variants={panel}
          initial="closed"
          animate="open"
          exit="exit"
          className="relative mt-12 scroll-mt-6 rounded-card border border-line bg-surface p-5 sm:mt-16 sm:p-8"
        >
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 id="how-heading" className="text-sm font-medium text-muted-foreground">
                How it&apos;s made
              </h2>
              <p className="mt-1 text-xl leading-snug font-light sm:text-2xl">{how.title}</p>
            </div>
            <button
              type="button"
              aria-label="Close"
              title="Close"
              onClick={() => setHowOpen(false)}
              className="press -mt-1 -mr-1 flex size-10 shrink-0 cursor-pointer items-center justify-center rounded-control text-muted-foreground outline-none hover:text-fg focus-visible:ring-2 focus-visible:ring-accent"
            >
              <X aria-hidden className="size-5" strokeWidth={1.75} />
            </button>
          </div>

          <ol className="mt-7 grid lg:mt-9 lg:auto-cols-fr lg:grid-flow-col lg:gap-x-6">
            {how.steps.map((item, index) => {
              const last = index === how.steps.length - 1;
              return (
                <motion.li
                  key={item.id}
                  variants={step}
                  className="relative pb-7 pl-8 last:pb-0 lg:pt-8 lg:pb-0 lg:pl-0"
                >
                  {/* The rail: a node per step and a line to the next one. */}
                  <span
                    aria-hidden
                    className="absolute top-1.5 left-0.5 size-2.5 rounded-full bg-accent lg:top-0 lg:left-0"
                  />
                  {!last && (
                    <span
                      aria-hidden
                      className="absolute top-6 bottom-1 left-[0.4375rem] w-px bg-line lg:top-1 lg:right-[-1rem] lg:bottom-auto lg:left-5 lg:h-px lg:w-auto"
                    />
                  )}
                  <div className="flex items-center gap-2.5 lg:flex-col lg:items-start lg:gap-3">
                    {item.logo && stackEntry(item.logo) ? (
                      <StackLogo id={item.logo} size={20} />
                    ) : (
                      // Keeps the titles of a horizontal strip on one line when a step has no logo.
                      <span aria-hidden className="hidden h-5 lg:block" />
                    )}
                    <h3 className="text-[0.9375rem] leading-snug font-medium">{item.title}</h3>
                  </div>
                  <p className="mt-1.5 text-[0.8125rem] leading-relaxed text-muted-foreground">
                    {item.detail}
                  </p>
                  {item.gaik && (
                    <p className="mt-2 font-mono text-xs break-words text-accent">{item.gaik}</p>
                  )}
                </motion.li>
              );
            })}
          </ol>

          {stack.length > 0 && (
            <motion.div variants={fade} className="mt-8 border-t border-line pt-6">
              <h3 className="text-[0.8125rem] text-muted-foreground">Built with</h3>
              <ul className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-3">
                {stack.map((id) => (
                  <li key={id} className="flex items-center gap-2 text-[0.8125rem]">
                    <StackLogo id={id} size={20} />
                    <span>{stackEntry(id)?.title}</span>
                  </li>
                ))}
              </ul>
            </motion.div>
          )}

          {funding && (
            <motion.div variants={fade} className="mt-8 border-t border-line pt-6">
              <Funding />
            </motion.div>
          )}
        </motion.section>
      )}
    </AnimatePresence>
  );
}
