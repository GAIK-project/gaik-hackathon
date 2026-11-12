"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { cn } from "cn";

import { Pressable } from "@/components/shell/pressable";

import { HUB_CARDS } from "./cards";
import { RingController } from "./ring-controller";
import { readMark } from "./ring-texture";

/**
 * The hero of the hub: the four demos as a ring of cards that turns.
 *
 * Drag or swipe turns it, a tap beside the front card brings that side's card forward, and
 * the front card is a link that opens its demo. The four links are a plain list: focusing
 * one turns the ring to its card, and the arrow keys move along the list. Until the
 * visitor touches it, the ring turns by itself.
 *
 * With reduced motion (decided in CSS, so nothing flashes) or without WebGL, the same four
 * cards are a 2 x 2 grid of links.
 */

const REDUCED = "(prefers-reduced-motion: reduce)";

function subscribeReduced(onChange: () => void): () => void {
  const query = window.matchMedia(REDUCED);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

export function Ring() {
  const reduced = useSyncExternalStore(
    subscribeReduced,
    () => window.matchMedia(REDUCED).matches,
    () => false,
  );
  const [failed, setFailed] = useState(false);
  const [front, setFront] = useState(0);
  const grid = reduced || failed;

  const rootRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const controllerRef = useRef<RingController | null>(null);

  useEffect(() => {
    const root = rootRef.current;
    const stage = stageRef.current;
    const canvas = canvasRef.current;
    if (grid || !root || !stage || !canvas) return;

    const controller = new RingController({
      stage,
      canvas,
      marks: Array.from(root.querySelectorAll<SVGSVGElement>("svg[data-mark]"), readMark),
      onFront: setFront,
      onFail: () => setFailed(true),
    });
    controllerRef.current = controller;
    if (process.env.NODE_ENV !== "production") {
      // Lets a screenshot script hold the ring in the middle of a turn.
      (window as unknown as { __hubRing?: RingController }).__hubRing = controller;
    }
    void controller.start();

    // Arrow keys turn the ring when the focus is on it or on nothing at all.
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      const active = document.activeElement;
      const inside = active instanceof HTMLElement && stage.contains(active);
      if (!inside && active && active !== document.body) return;
      const rect = stage.getBoundingClientRect();
      if (rect.bottom < 0 || rect.top > window.innerHeight) return;
      event.preventDefault();
      const direction = event.key === "ArrowRight" ? 1 : -1;
      if (inside) {
        // Focus follows the ring: the next link takes it, and its focus turns the ring.
        const links = Array.from(stage.querySelectorAll<HTMLAnchorElement>("a"));
        const next = (links.indexOf(active as HTMLAnchorElement) + direction + links.length) % links.length;
        links[next]?.focus();
      } else {
        controller.step(direction);
      }
    };
    window.addEventListener("keydown", onKey);

    return () => {
      window.removeEventListener("keydown", onKey);
      controller.dispose();
      controllerRef.current = null;
    };
  }, [grid]);

  return (
    <div ref={rootRef}>
      <div
        ref={stageRef}
        style={{ "--card-w": "min(68%, 22.5rem)", "--card-h": "86%" } as React.CSSProperties}
        className={cn(
          "relative h-[min(clamp(22rem,56svh,36rem),106vw)] cursor-grab touch-pan-y select-none data-dragging:cursor-grabbing motion-reduce:hidden",
          grid && "hidden",
        )}
      >
        <canvas ref={canvasRef} aria-hidden className="pointer-events-none absolute top-0 left-0 block" />
        <nav aria-label="Demos">
          <ul>
            {HUB_CARDS.map((card, index) => (
              <li key={card.demo.id}>
                <Link
                  href={card.demo.href}
                  draggable={false}
                  onFocus={() => controllerRef.current?.goTo(index)}
                  className={cn(
                    "absolute top-1/2 left-1/2 h-(--card-h) w-(--card-w) -translate-1/2 cursor-pointer rounded-card outline-offset-4",
                    index !== front && "pointer-events-none",
                  )}
                >
                  <span className="sr-only">
                    {card.demo.title}: {card.line}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </div>

      <nav
        aria-label="Demos"
        className={cn("mx-auto w-full max-w-2xl px-4 sm:px-8", grid ? "block" : "hidden motion-reduce:block")}
      >
        <ul className="grid grid-cols-2 gap-3 sm:gap-4">
          {HUB_CARDS.map((card) => {
            const Icon = card.demo.icon;
            return (
              <li key={card.demo.id}>
                <Pressable asChild>
                  <Link
                    href={card.demo.href}
                    className="relative flex aspect-3/4 flex-col justify-end overflow-hidden rounded-card border border-line bg-surface p-3.5 text-left sm:p-6"
                  >
                    <img
                      src={card.art}
                      alt=""
                      width={720}
                      height={960}
                      loading="lazy"
                      decoding="async"
                      draggable={false}
                      className="absolute inset-0 size-full object-cover"
                    />
                    <span
                      aria-hidden
                      className="absolute inset-0 bg-linear-to-b from-transparent from-30% via-bg/75 via-62% to-bg/95"
                    />
                    <Icon
                      data-mark
                      aria-hidden
                      strokeWidth={1}
                      className="relative -ml-0.5 size-9 sm:size-14"
                    />
                    <span className="relative mt-2 text-[1.0625rem] leading-tight font-normal sm:mt-3 sm:text-[1.75rem] sm:font-light">
                      {card.demo.title}
                    </span>
                    <span className="relative mt-1 text-[0.8125rem] leading-snug text-muted-foreground sm:mt-1.5 sm:text-[0.9375rem]">
                      {card.line}
                    </span>
                  </Link>
                </Pressable>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}
