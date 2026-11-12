import { ViewTransition } from "react";

/**
 * The view transition of a page change: the old page fades out, the new one fades in
 * (`page-out` / `page-in` in globals.css). Moving between the dock's items is lateral, so
 * there is no directional slide.
 *
 * It must be the outermost thing a page renders: a <ViewTransition> nested under another
 * element of the same page never gets its enter and exit. PageShell already includes it;
 * a page that does not use PageShell (the hub) wraps itself in this.
 */
export function PageTransition({ children }: { children: React.ReactNode }) {
  return (
    <ViewTransition enter="page-in" exit="page-out" default="none">
      {children}
    </ViewTransition>
  );
}
