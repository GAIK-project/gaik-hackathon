"use client";

import { Quote } from "lucide-react";
import { cn } from "cn";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

/**
 * A quiet chip that opens the source of a value on press: the words of a transcript a
 * field rests on, the provision behind an obligation. Detail stays behind the chip instead
 * of lying open on the page. A press works on a touch screen, so nothing depends on hover.
 *
 *   <SourceChip label="Liite III, 4 kohta" title="Liite III, 4 kohdan a alakohta">
 *     <blockquote>…</blockquote>
 *   </SourceChip>
 *
 * Without children it is a plain button: pass `onPress` to open the source somewhere else
 * (a sheet, a side panel).
 */
export function SourceChip({
  label,
  title,
  children,
  onPress,
  icon,
  className,
}: {
  /** Short text on the chip. */
  label: string;
  /** Heading inside the opened source; defaults to the label. */
  title?: string;
  /** The source itself. */
  children?: React.ReactNode;
  onPress?: () => void;
  /** Replaces the quote mark, for example a <StackLogo>. */
  icon?: React.ReactNode;
  className?: string;
}) {
  const chip = (
    <button
      type="button"
      onClick={onPress}
      className={cn(
        "press inline-flex h-7 max-w-full cursor-pointer touch-manipulation items-center gap-1.5 rounded-md bg-surface-2 px-2 align-middle text-[0.8125rem] text-muted-foreground outline-none select-none",
        "transition-[transform,color,background-color] duration-150 ease-stage hover:text-fg focus-visible:ring-2 focus-visible:ring-accent",
        "aria-expanded:bg-line aria-expanded:text-fg",
        className,
      )}
    >
      {icon ?? <Quote aria-hidden className="size-3.5 shrink-0" strokeWidth={1.75} />}
      <span className="truncate">{label}</span>
    </button>
  );

  if (!children) return chip;

  return (
    <Popover>
      <PopoverTrigger asChild>{chip}</PopoverTrigger>
      <PopoverContent
        align="start"
        collisionPadding={12}
        className="w-[min(22rem,calc(100vw-1.5rem))] gap-2 p-4 text-[0.875rem] leading-relaxed ring-line"
      >
        <p className="text-[0.8125rem] font-medium text-muted-foreground">{title ?? label}</p>
        <div>{children}</div>
      </PopoverContent>
    </Popover>
  );
}
