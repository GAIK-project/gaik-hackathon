"use client";

import { ViewTransition } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Layers } from "lucide-react";
import { cn } from "cn";

import { normalizePath, useDock } from "@/components/shell/shell-provider";
import { DEMOS } from "@/lib/demos";

/**
 * The one piece of navigation: a bottom pill on every page. GAIK mark (home), the four
 * demos, and the "How it's made" toggle. Its content never changes between pages, so
 * its width does not either. The background of the active item is a shared element of the
 * page's view transition and glides to the next item. The pill itself has a
 * view-transition-name (`dock`), so it stays put, above the pages that fade under it.
 *
 * A page hides it with `useDock().setHidden(true)` (a live call).
 */

const itemClass = cn(
  "relative isolate flex shrink-0 cursor-pointer touch-manipulation select-none items-center justify-center rounded-full outline-none",
  "h-[3.25rem] min-w-12 flex-col gap-0.5 px-2 text-[0.6875rem] leading-none font-medium",
  "sm:h-11 sm:flex-row sm:gap-2 sm:px-4 sm:text-sm",
  "transition-[transform,color] duration-150 ease-stage active:scale-[0.96]",
  "focus-visible:ring-2 focus-visible:ring-accent",
);

function ActivePill() {
  return (
    <ViewTransition name="dock-pill" share="dock-pill" default="none">
      <span aria-hidden className="absolute inset-0 -z-10 rounded-full bg-fg/10" />
    </ViewTransition>
  );
}

export function Dock() {
  const path = normalizePath(usePathname());
  const { hidden, howOpen, toggleHow } = useDock();

  return (
    <nav
      aria-label="Main menu"
      inert={hidden}
      className={cn(
        "pointer-events-none fixed inset-x-0 bottom-(--dock-gap) z-50 flex justify-center px-3",
        "transition-[transform,opacity] ease-stage",
        hidden
          ? "translate-y-[calc(100%+var(--dock-gap))] opacity-0 duration-200"
          : "translate-y-0 opacity-100 duration-300",
      )}
    >
      <div className="pointer-events-auto flex max-w-full items-center gap-0.5 rounded-full border border-line bg-surface-2 p-1.5 [view-transition-name:dock]">
        <Link
          href="/"
          aria-label="Home"
          aria-current={path === "/" ? "page" : undefined}
          className={cn(itemClass, "w-[3.25rem] px-0 sm:w-11 sm:px-0")}
        >
          {path === "/" && <ActivePill />}
          <img
            src="/brand/gaik-logo-letter-only.png"
            alt=""
            width={28}
            height={28}
            draggable={false}
            className="size-7"
          />
        </Link>

        <span aria-hidden className="mx-1 hidden h-6 w-px bg-line sm:block" />

        {DEMOS.map((demo) => {
          const active = path === demo.href;
          const Icon = demo.icon;
          return (
            <Link
              key={demo.id}
              href={demo.href}
              aria-current={active ? "page" : undefined}
              className={cn(itemClass, active ? "text-fg" : "text-muted-foreground hover:text-fg")}
            >
              {active && <ActivePill />}
              <Icon aria-hidden className="size-5 sm:size-[1.125rem]" strokeWidth={1.75} />
              <span>{demo.label}</span>
            </Link>
          );
        })}

        <span aria-hidden className="mx-1 hidden h-6 w-px bg-line sm:block" />

        <button
          type="button"
          aria-pressed={howOpen}
          aria-label="How it's made"
          onClick={toggleHow}
          className={cn(itemClass, howOpen ? "text-accent" : "text-muted-foreground hover:text-fg")}
        >
          <Layers aria-hidden className="size-5 sm:size-[1.125rem]" strokeWidth={1.75} />
          <span className="sm:hidden">How</span>
          <span className="hidden sm:inline">How it&apos;s made</span>
        </button>
      </div>
    </nav>
  );
}
