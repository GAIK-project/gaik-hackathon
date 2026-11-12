"use client";

import { useEffect, useRef } from "react";
import { Phone, PhoneOff } from "lucide-react";
import { cn } from "cn";

/**
 * The focal point of the page: one orb that is both the control and the status. A 2D
 * canvas draws a soft body whose outline moves with the real audio level (the microphone
 * while the agent listens, the agent's voice while it speaks). No WebGL: three layered
 * gradients and one path per frame.
 */
export function Orb({
  active,
  busy,
  level,
  onPress,
  label,
  className,
}: {
  /** A call is on. */
  active: boolean;
  /** Connecting or saving: the orb turns slowly and takes no level. */
  busy: boolean;
  level: () => number;
  onPress: () => void;
  label: string;
  className?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const stateRef = useRef({ active, busy });
  const levelRef = useRef(level);

  useEffect(() => {
    stateRef.current = { active, busy };
    levelRef.current = level;
  }, [active, busy, level]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const styles = getComputedStyle(document.documentElement);
    const brandA = styles.getPropertyValue("--brand-a").trim() || "#0079c2";
    const brandB = styles.getPropertyValue("--brand-b").trim() || "#8a2387";

    let frame = 0;
    let smooth = 0;
    let energy = 0;
    let size = 0;

    function resize() {
      if (!canvas || !ctx) return;
      const rect = canvas.getBoundingClientRect();
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      size = rect.width;
      canvas.width = Math.round(rect.width * ratio);
      canvas.height = Math.round(rect.height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    }

    function draw(now: number) {
      if (!ctx) return;
      const t = now / 1000;
      const { active: on, busy: waiting } = stateRef.current;
      const raw = on && !waiting ? Math.min(1, Math.max(0, levelRef.current())) : 0;
      // Fast attack, slow release, so speech reads as movement, not flicker.
      smooth += (raw - smooth) * (raw > smooth ? 0.35 : 0.08);
      energy += ((on ? 1 : 0) - energy) * 0.06;

      const c = size / 2;
      const base = size * 0.31;
      const breath = reduced ? 0 : Math.sin(t * (waiting ? 2.4 : 1.1)) * 0.02;
      const radius = base * (1 + breath + smooth * 0.22);

      ctx.clearRect(0, 0, size, size);

      // Halo: grows with the level.
      const haloR = Math.min(size / 2, radius * (1.4 + smooth * 0.3 + energy * 0.1));
      const halo = ctx.createRadialGradient(c, c, radius * 0.6, c, c, haloR);
      halo.addColorStop(0, `rgba(72, 160, 255, ${0.26 + energy * 0.14 + smooth * 0.2})`);
      halo.addColorStop(0.55, `rgba(138, 35, 135, ${0.12 + smooth * 0.12})`);
      halo.addColorStop(1, "rgba(138, 35, 135, 0)");
      ctx.fillStyle = halo;
      ctx.beginPath();
      ctx.arc(c, c, haloR, 0, Math.PI * 2);
      ctx.fill();

      // Body: a circle whose edge carries three slow waves, deeper with the level.
      const wobble = reduced ? 0 : radius * (0.012 + energy * 0.012 + smooth * 0.11);
      const steps = 72;
      ctx.beginPath();
      for (let i = 0; i <= steps; i += 1) {
        const a = (i / steps) * Math.PI * 2;
        const r =
          radius +
          wobble *
            (Math.sin(a * 3 + t * 1.7) * 0.5 +
              Math.sin(a * 5 - t * 2.3) * 0.3 +
              Math.sin(a * 2 + t * 0.9) * 0.2);
        const x = c + Math.cos(a) * r;
        const y = c + Math.sin(a) * r;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.closePath();
      const spin = reduced ? 0.6 : t * (waiting ? 1.2 : 0.25);
      const body = ctx.createLinearGradient(
        c + Math.cos(spin) * radius,
        c + Math.sin(spin) * radius,
        c - Math.cos(spin) * radius,
        c - Math.sin(spin) * radius,
      );
      body.addColorStop(0, brandA);
      body.addColorStop(0.5, "#3f8fe6");
      body.addColorStop(1, brandB);
      ctx.fillStyle = body;
      ctx.fill();

      // Soft light from the upper left, so it reads as a body and not a disc.
      const light = ctx.createRadialGradient(
        c - radius * 0.35,
        c - radius * 0.4,
        0,
        c - radius * 0.2,
        c - radius * 0.25,
        radius * 1.1,
      );
      light.addColorStop(0, "rgba(255, 255, 255, 0.34)");
      light.addColorStop(0.5, "rgba(255, 255, 255, 0.06)");
      light.addColorStop(1, "rgba(10, 14, 40, 0.28)");
      ctx.fillStyle = light;
      ctx.fill();

      if (!reduced) frame = requestAnimationFrame(draw);
    }

    resize();
    const observer = new ResizeObserver(() => {
      resize();
      if (reduced) draw(0);
    });
    observer.observe(canvas);
    frame = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, []);

  const Icon = active ? PhoneOff : Phone;

  return (
    <button
      type="button"
      onClick={onPress}
      aria-label={label}
      aria-pressed={active}
      className={cn(
        "group relative grid aspect-square w-[min(64vw,17rem)] cursor-pointer touch-manipulation place-items-center rounded-full outline-none select-none",
        "transition-transform duration-200 ease-stage active:scale-[0.96]",
        "focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-4 focus-visible:ring-offset-bg",
        className,
      )}
    >
      <canvas ref={canvasRef} aria-hidden className="absolute inset-0 size-full" />
      <Icon
        aria-hidden
        strokeWidth={1.5}
        className="relative size-8 text-white/90 transition-opacity duration-200 ease-stage group-active:opacity-70"
      />
    </button>
  );
}
