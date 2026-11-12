import * as React from "react";
import { Slot } from "radix-ui";
import { cn } from "cn";

/**
 * Anything pressable that is not a shadcn <Button>: it answers a press with a slight scale.
 * Renders a <button>; with `asChild` it passes the behaviour to its child, for a link:
 *
 *   <Pressable asChild><Link href="/soita">…</Link></Pressable>
 */
export function Pressable({
  asChild = false,
  className,
  ...props
}: React.ComponentProps<"button"> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "button";
  return (
    <Comp
      data-slot="pressable"
      {...(asChild ? {} : { type: "button" as const })}
      className={cn(
        "cursor-pointer touch-manipulation select-none outline-none",
        "transition-[transform,background-color,border-color,color,opacity] duration-150 ease-stage",
        "active:scale-[0.97] focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
        "disabled:pointer-events-none disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}
