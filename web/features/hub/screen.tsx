import Link from "next/link";

import { Funding } from "@/components/shell/funding";
import { HowPanel } from "@/components/shell/how-panel";
import { PageTransition } from "@/components/shell/page-transition";
import { Pressable } from "@/components/shell/pressable";
import { DEMOS } from "@/lib/demos";

import { how } from "./how";

const STATEMENT = "Neljä demoa gaik-kirjaston komponenteista";

/**
 * The hub: brand first, no call to action in the hero. The four demos are plain cards for
 * now; the hub's owner replaces them with the ring of section 14 and adds the diagram and
 * the trailer. The footer with the GAIK logo and the EU emblem stays.
 */
export function HubScreen() {
  const words = STATEMENT.split(" ");

  return (
    <PageTransition>
      <main className="mx-auto flex min-h-dvh w-full max-w-6xl flex-col px-4 pb-(--dock-clearance) sm:px-8">
        {/* Full bleed, so the glow is clipped at the edge of the screen and not at the page gutter. */}
        <section className="-mx-4 flex flex-col items-center overflow-x-clip px-4 pt-[12vh] text-center sm:-mx-8 sm:px-8 sm:pt-[10vh]">
          <div className="stage-glow animate-rise">
            <img
              src="/brand/gaik_logo_fullwhite.svg"
              alt="GAIK, Generative AI-Enhanced Knowledge Management in Business"
              width={1376}
              height={702}
              fetchPriority="high"
              className="h-auto w-[min(72vw,21rem)]"
            />
          </div>
          {/* The one orchestrated entrance of the page: the statement rises word by word. */}
          <h1 className="mt-10 max-w-[18ch] text-[clamp(1.75rem,1rem+3vw,2.75rem)] leading-[1.12] font-light tracking-[-0.02em] text-balance sm:max-w-none">
            {words.map((word, index) => (
              <span key={index}>
                <span
                  className="inline-block animate-word-rise"
                  style={{ animationDelay: `${200 + index * 80}ms` }}
                >
                  {word}
                </span>
                {index < words.length - 1 && " "}
              </span>
            ))}
          </h1>
        </section>

        <nav aria-label="Demot" className="mt-14 grid gap-3 sm:mt-16 sm:grid-cols-2 lg:grid-cols-4">
          {DEMOS.map((demo) => {
            const Icon = demo.icon;
            return (
              <Pressable key={demo.id} asChild>
                <Link
                  href={demo.href}
                  className="flex flex-col rounded-card border border-line bg-surface p-5 hover:bg-surface-2"
                >
                  <Icon aria-hidden className="size-6 text-muted-foreground" strokeWidth={1.5} />
                  <span className="mt-5 text-lg leading-snug font-medium">{demo.title}</span>
                  <span className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                    {demo.line}
                  </span>
                </Link>
              </Pressable>
            );
          })}
        </nav>

        <HowPanel how={how} funding={false} />

        <footer className="mt-auto flex flex-col gap-5 pt-16 sm:flex-row sm:items-end sm:justify-between sm:pt-14">
          <Funding />
          <p className="text-[0.8125rem] leading-relaxed text-balance text-muted-foreground sm:text-right">
            Haaga-Helian GAIK-hanke. Kaikki esimerkkisisältö on keksittyä.
          </p>
        </footer>
      </main>
    </PageTransition>
  );
}
