import { cn } from "cn";

import { stackEntry, stackSrc } from "@/lib/stack";

/**
 * One official logo from `public/stack`, by id (see `lib/stack.ts`). Always an <img>, so
 * gradient and mask ids inside the SVG files cannot collide. `size` is the height in px;
 * the width follows the mark's own proportions. An unknown id renders nothing.
 *
 * Decorative by default (the name usually stands next to it). Pass `label` to give the
 * image an accessible name when it stands alone.
 */
export function StackLogo({
  id,
  size = 20,
  label,
  className,
}: {
  id: string;
  size?: number;
  /** true: use the logo's title as its name; a string: use that. */
  label?: boolean | string;
  className?: string;
}) {
  const entry = stackEntry(id);
  if (!entry) return null;
  const alt = label === true ? entry.title : label || "";

  const image = (
    <img
      src={stackSrc(entry)}
      alt={alt}
      height={size}
      loading="lazy"
      decoding="async"
      draggable={false}
      style={{ height: entry.plate ? size - 4 : size, width: "auto" }}
      className={cn("block max-w-none shrink-0 select-none", !entry.plate && className)}
    />
  );

  if (!entry.plate) return image;

  // A mark that exists only for light backgrounds keeps its colours on a light plate.
  return (
    <span
      style={{ height: size + 4 }}
      className={cn(
        "-my-0.5 inline-flex shrink-0 items-center rounded-sm bg-fg px-1.5",
        className,
      )}
    >
      {image}
    </span>
  );
}
