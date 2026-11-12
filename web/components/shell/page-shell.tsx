import { cn } from "cn";

import { HowPanel } from "@/components/shell/how-panel";
import { PageTransition } from "@/components/shell/page-transition";
import type { How } from "@/lib/types";

/**
 * The frame of every demo page: a one-line title, an optional one-line lede, the working
 * surface, then "How it's made". No top bar; the dock is in the root layout. The
 * bottom padding keeps the last element clear of the dock.
 */
export function PageShell({
  title,
  lede,
  how,
  children,
  className,
}: {
  title: string;
  lede?: string;
  how: How;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <PageTransition>
      <main
        className={cn(
          "mx-auto flex min-h-dvh w-full max-w-6xl flex-col px-4 pt-10 pb-(--dock-clearance) sm:px-8 sm:pt-16",
          className,
        )}
      >
        <header>
          <h1 className="text-[clamp(2rem,1.2rem+3.4vw,3.5rem)] leading-[1.08] font-light tracking-[-0.02em] text-balance">
            {title}
          </h1>
          {lede && (
            <p className="mt-3 max-w-[62ch] text-[1.0625rem] text-muted-foreground">{lede}</p>
          )}
        </header>
        <div className="mt-8 sm:mt-10">{children}</div>
        <HowPanel how={how} />
      </main>
    </PageTransition>
  );
}
