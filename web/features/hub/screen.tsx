import { Funding } from "@/components/shell/funding";
import { PageTransition } from "@/components/shell/page-transition";

import { Diagram } from "./diagram";
import { Ring } from "./ring";
import { TRAILER } from "./trailer";
import { TrailerFilm } from "./trailer-film";

const STATEMENT = "Neljä demoa gaik-kirjaston komponenteista";

/**
 * The hub (docs/ARCHITECTURE.md section 14): brand first, no call to action. The GAIK logo
 * and one statement, then the four demos as a ring of cards (the hero), how the whole app
 * is built as one diagram, the trailer when it exists, and the footer with the GAIK logo
 * and the EU emblem.
 */
export function HubScreen() {
  const words = STATEMENT.split(" ");

  return (
    <PageTransition>
      <main className="flex min-h-dvh w-full flex-col pb-(--dock-clearance)">
        {/* Full width, so the ring and its glows run to the edges of the screen. */}
        <section className="flex min-h-svh flex-col overflow-x-clip pt-[6svh] pb-(--dock-clearance) text-center sm:pt-[5svh]">
          <img
            src="/brand/gaik_logo_fullwhite.svg"
            alt="GAIK, Generative AI-Enhanced Knowledge Management in Business"
            width={1376}
            height={702}
            fetchPriority="high"
            className="relative z-10 mx-auto h-auto w-[min(44vw,12.5rem)] animate-rise"
          />
          {/* The one orchestrated entrance of the page: the statement rises word by word,
              and the ring whips in under it. */}
          <h1 className="relative z-10 mx-auto mt-5 max-w-[18ch] px-4 text-[clamp(1.625rem,1rem+2.6vw,2.5rem)] leading-[1.12] font-light tracking-[-0.02em] text-balance sm:mt-6 sm:max-w-none">
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
          {/* The ring takes the middle of what is left above the dock. */}
          <div className="mt-4 flex flex-1 flex-col justify-center sm:mt-6">
            <Ring />
          </div>
        </section>

        <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col px-4 sm:px-8">
          <Diagram />
          {TRAILER && <TrailerFilm trailer={TRAILER} />}

          <footer className="mt-auto flex flex-col gap-5 pt-16 sm:flex-row sm:items-end sm:justify-between sm:pt-14">
            <Funding />
            <p className="text-[0.8125rem] leading-relaxed text-balance text-muted-foreground sm:text-right">
              Haaga-Helian GAIK-hanke. Kaikki esimerkkisisältö on keksittyä.
            </p>
          </footer>
        </div>
      </main>
    </PageTransition>
  );
}
