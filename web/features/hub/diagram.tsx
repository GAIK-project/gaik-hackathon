"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Mail } from "lucide-react";
import { cn } from "cn";

import { useDock } from "@/components/shell/shell-provider";
import { StackLogo } from "@/components/shell/stack-logo";
import { stackEntry } from "@/lib/stack";

/**
 * "Näin tämä on tehty" on the hub: the whole app as one diagram, with the real logos.
 * Who calls the server (browser and phone, agents over MCP, the WhatsApp bridge on the
 * owner's machine), what runs inside CSC Rahti (the FastAPI server with the gaik
 * components it calls, gaik-decide), and what the server calls outside (Azure OpenAI, the
 * mail relay, Jev). Facts: docs/ARCHITECTURE.md sections 2, 12, 13 and 15.
 *
 * It draws itself once, left to right, when it scrolls into view: boxes rise, wires grow
 * from their source. With reduced motion everything is there from the start (CSS, so it
 * does not depend on the observer). The dock's "Näin tämä on tehty" scrolls here.
 */

/** gaik components the server calls (sections 13 and 15.5). */
const COMPONENTS = [
  "Transcriber",
  "DataExtractor",
  "SchemaGenerator",
  "SourceTracing",
  "JevClassifier",
  "Embedder",
  "VectorStore",
  "parsers",
];

/** What the app asks of Azure OpenAI: chat, realtime, transcription, embeddings. */
const AZURE_USES = ["Kieli", "Puhe", "Litterointi", "Upotukset"];

/** Everything the app is built with, for the strip under the diagram. */
const BUILT_WITH = [
  "nextjs",
  "react",
  "tailwindcss",
  "shadcn-ui",
  "motion",
  "threejs",
  "bun",
  "fastapi",
  "python",
  "uv",
  "sqlite",
  "docker",
  "openshift",
];

/** Delay between the stages of the drawing, ms. */
const STAGE_MS = 130;

const drawn = "group-data-drawn/diagram:translate-y-0 group-data-drawn/diagram:opacity-100";
const rises = cn(
  "translate-y-2.5 opacity-0 transition-[opacity,translate] duration-500 ease-stage",
  "motion-reduce:translate-y-0 motion-reduce:opacity-100",
  drawn,
);

function delay(stage: number, extra = 0): React.CSSProperties {
  return { transitionDelay: `${stage * STAGE_MS + extra}ms` };
}

function Box({
  stage,
  logos = [],
  icon,
  title,
  mono = false,
  note,
  className,
  children,
}: {
  stage: number;
  logos?: string[];
  icon?: React.ReactNode;
  title: string;
  mono?: boolean;
  note?: string;
  className?: string;
  children?: React.ReactNode;
}) {
  return (
    <div
      style={delay(stage)}
      className={cn("rounded-card border border-line bg-surface p-4", rises, className)}
    >
      <div className="flex items-center gap-2.5">
        {logos.map((id) => (
          <StackLogo key={id} id={id} size={20} />
        ))}
        {icon}
        <h3 className={cn("text-[0.9375rem] leading-snug font-medium", mono && "font-mono text-sm")}>
          {title}
        </h3>
      </div>
      {note && <p className="mt-1.5 text-[0.8125rem] leading-snug text-muted-foreground">{note}</p>}
      {children}
    </div>
  );
}

/** A connection, drawn from its source: left to right, or downward when `down` is set. */
function Wire({ stage, down = false, className }: { stage: number; down?: boolean; className?: string }) {
  const Tip = down ? ChevronDown : ChevronRight;
  return (
    <span
      aria-hidden
      className={cn(
        "relative flex items-center justify-center",
        down ? "h-9 w-3 flex-col" : "h-3 w-full",
        className,
      )}
    >
      <span
        style={delay(stage)}
        className={cn(
          "bg-muted-foreground/45 transition-[scale] duration-500 ease-stage",
          down
            ? "h-full w-px origin-top scale-y-0 group-data-drawn/diagram:scale-y-100 motion-reduce:scale-y-100"
            : "h-px w-full origin-left scale-x-0 group-data-drawn/diagram:scale-x-100 motion-reduce:scale-x-100",
        )}
      />
      <Tip
        style={delay(stage, 320)}
        strokeWidth={1.5}
        className={cn(
          "absolute size-3.5 text-muted-foreground opacity-0 transition-opacity duration-200",
          "group-data-drawn/diagram:opacity-100 motion-reduce:opacity-100",
          down ? "-bottom-1.5" : "-right-1.5",
        )}
      />
    </span>
  );
}

function RahtiHeading({ stage, className }: { stage: number; className?: string }) {
  return (
    <div style={delay(stage)} className={cn("flex items-center gap-2.5", rises, className)}>
      <StackLogo id="csc" size={20} />
      <h3 className="text-[0.9375rem] leading-snug font-medium">CSC Rahti</h3>
      <span className="text-[0.8125rem] text-muted-foreground">Yksi kontti</span>
      <span className="ml-auto flex items-center gap-2.5">
        <StackLogo id="openshift" size={18} label />
        <StackLogo id="docker" size={18} label />
      </span>
    </div>
  );
}

function Server({ stage, className }: { stage: number; className?: string }) {
  return (
    <Box
      stage={stage}
      logos={["fastapi"]}
      title="FastAPI-palvelin"
      note="Sivut, rajapinta ja MCP"
      className={className}
    >
      <div className="mt-4 border-t border-line pt-4">
        <div className="flex items-center gap-2.5">
          <StackLogo id="python" size={18} />
          <span className="font-mono text-sm">gaik</span>
          <span className="text-[0.8125rem] text-muted-foreground">Komponentit PyPI:stä</span>
        </div>
        <ul className="mt-3 flex flex-wrap gap-1.5">
          {COMPONENTS.map((name) => (
            <li
              key={name}
              className="rounded-sm bg-surface-2 px-2 py-1 font-mono text-xs leading-none text-accent"
            >
              {name}
            </li>
          ))}
        </ul>
      </div>
      <div className="mt-4 flex items-center gap-2.5 border-t border-line pt-4">
        <StackLogo id="sqlite" size={18} />
        <span className="text-[0.8125rem] text-muted-foreground">SQLite ja tiedostot</span>
      </div>
    </Box>
  );
}

function Azure({ stage }: { stage: number }) {
  return (
    <Box stage={stage} logos={["azure", "openai"]} title="Azure OpenAI">
      <ul className="mt-2.5 grid grid-cols-2 gap-x-3 gap-y-1 text-[0.8125rem] leading-snug text-muted-foreground">
        {AZURE_USES.map((use) => (
          <li key={use}>{use}</li>
        ))}
      </ul>
    </Box>
  );
}

const browser = { logos: ["nextjs", "react"], title: "Selain ja puhelin", note: "Next.js-sovellus" };
const agents = { logos: ["mcp", "claude"], title: "Agentit", note: "MCP-asiakkaat, esimerkiksi Claude" };
const whatsapp = { logos: ["whatsapp"], title: "WhatsApp", note: "Silta omistajan koneella" };
const decide = { title: "gaik-decide", mono: true, note: "Tyypitetyt päätökset" };
const mail = { title: "Sähköposti", note: "CSC:n postinvälitys" };
const jev = { logos: ["typesafe"], title: "Jev", note: "TypeSafe, Yhdysvallat" };

function MailIcon() {
  return <Mail aria-hidden className="size-5 text-muted-foreground" strokeWidth={1.5} />;
}

const frame = "pointer-events-none rounded-card border border-dashed border-line";

export function Diagram() {
  const ref = useRef<HTMLElement>(null);
  const [isDrawn, setDrawn] = useState(false);
  const { howOpen } = useDock();
  const lastHow = useRef(howOpen);

  // Draw once, when a good part of the diagram is on screen.
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        setDrawn(true);
        observer.disconnect();
      },
      { rootMargin: "0px 0px -22% 0px" },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // The dock's "Näin tämä on tehty" brings the visitor here.
  useEffect(() => {
    if (howOpen === lastHow.current) return;
    lastHow.current = howOpen;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    ref.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }, [howOpen]);

  const builtWith = BUILT_WITH.filter((id) => stackEntry(id));

  return (
    <section
      ref={ref}
      aria-labelledby="hub-how"
      data-drawn={isDrawn || undefined}
      className="group/diagram relative z-10 mt-8 scroll-mt-8 sm:mt-12"
    >
      <h2
        id="hub-how"
        className="text-[clamp(1.5rem,1rem+2vw,2.25rem)] leading-tight font-light tracking-[-0.015em]"
      >
        Näin tämä on tehty
      </h2>

      {/* Wide screens: callers on the left, Rahti in the middle, outside services on the right. */}
      <div className="mt-10 hidden grid-cols-[minmax(0,13.5rem)_3.25rem_minmax(0,1fr)_2.5rem_minmax(0,9.5rem)_3.25rem_minmax(0,13.5rem)] grid-rows-[auto_auto_auto_auto] items-center gap-y-4 lg:grid">
        <div style={delay(2)} className={cn(frame, rises, "col-[3/6] row-[1/5] -m-4 self-stretch")} />
        <RahtiHeading stage={2} className="col-[3/6] row-1" />

        <Box stage={0} {...browser} className="col-1 row-2" />
        <Box stage={0} {...agents} className="col-1 row-3" />
        <Box stage={0} {...whatsapp} className="col-1 row-4" />
        <Wire stage={1} className="col-2 row-2" />
        <Wire stage={1} className="col-2 row-3" />
        <Wire stage={1} className="col-2 row-4" />

        <Server stage={2} className="col-3 row-[2/5] self-stretch" />
        <Wire stage={3} className="col-4 row-4" />
        <Box stage={4} {...decide} className="col-5 row-4" />

        <Wire stage={3} className="col-[4/7] row-2" />
        <Wire stage={3} className="col-[4/7] row-3" />
        <Wire stage={5} className="col-6 row-4" />
        <div className="col-7 row-2">
          <Azure stage={5} />
        </div>
        <Box stage={5} icon={<MailIcon />} {...mail} className="col-7 row-3" />
        <Box stage={6} {...jev} className="col-7 row-4" />
      </div>

      {/* Narrow screens: the same parts, top to bottom. */}
      <div className="mt-8 flex flex-col items-center lg:hidden">
        <div className="grid w-full gap-3 sm:grid-cols-3">
          <Box stage={0} {...browser} />
          <Box stage={0} {...agents} />
          <Box stage={0} {...whatsapp} />
        </div>
        <Wire stage={1} down />
        <div className="relative mt-2.5 w-full p-3 sm:p-4">
          <div style={delay(2)} className={cn(frame, rises, "absolute inset-0")} />
          <RahtiHeading stage={2} className="mb-3 px-1" />
          <Server stage={2} />
          <div className="flex flex-col items-center">
            <Wire stage={3} down />
            <Box stage={4} {...decide} className="mt-2.5 w-full" />
          </div>
        </div>
        <Wire stage={5} down />
        <div className="mt-2.5 grid w-full gap-3 sm:grid-cols-3">
          <Azure stage={6} />
          <Box stage={6} icon={<MailIcon />} {...mail} />
          <Box stage={6} {...jev} />
        </div>
      </div>

      <div style={delay(7)} className={cn("mt-12 border-t border-line pt-6", rises)}>
        <h3 className="text-[0.8125rem] text-muted-foreground">Rakennettu näillä</h3>
        <ul className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-3">
          {builtWith.map((id) => (
            <li key={id} className="flex items-center gap-2 text-[0.8125rem]">
              <StackLogo id={id} size={20} />
              <span>{stackEntry(id)?.title}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
