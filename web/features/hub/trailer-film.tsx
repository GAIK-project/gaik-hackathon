"use client";

import { useRef, useState } from "react";
import { Play } from "lucide-react";

import { Pressable } from "@/components/shell/pressable";

import type { Trailer } from "./trailer";

/**
 * The trailer: a poster with one play control. Nothing loads and nothing sounds until the
 * visitor presses play; then the film plays with sound and the browser's own controls.
 * When it ends, the poster and the play control come back.
 */
export function TrailerFilm({ trailer }: { trailer: Trailer }) {
  const video = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);

  const play = () => {
    const element = video.current;
    if (!element) return;
    setPlaying(true);
    element.muted = false;
    void element.play().catch(() => setPlaying(false));
  };

  const reset = () => {
    setPlaying(false);
    // Loading again brings the poster back.
    video.current?.load();
  };

  return (
    <section aria-labelledby="hub-trailer" className="mt-20 sm:mt-28">
      <h2
        id="hub-trailer"
        className="text-[clamp(1.5rem,1rem+2vw,2.25rem)] leading-tight font-light tracking-[-0.015em]"
      >
        Traileri
      </h2>
      <div className="relative mt-8 aspect-video overflow-hidden rounded-card border border-line bg-surface">
        <video
          ref={video}
          src={trailer.src}
          poster={trailer.poster}
          preload="none"
          playsInline
          controls={playing}
          onEnded={reset}
          className="size-full object-cover"
        />
        {!playing && (
          <Pressable
            onClick={play}
            aria-label="Toista traileri"
            title="Toista traileri"
            className="absolute inset-0 flex items-center justify-center"
          >
            <span className="flex size-16 items-center justify-center rounded-card bg-fg text-accent-fg sm:size-20">
              <Play aria-hidden className="ml-1 size-7 sm:size-8" strokeWidth={1.5} fill="currentColor" />
            </span>
          </Pressable>
        )}
      </div>
    </section>
  );
}
