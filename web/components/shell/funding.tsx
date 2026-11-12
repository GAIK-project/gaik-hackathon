import { cn } from "cn";

/**
 * The GAIK logo and the "Co-funded by the European Union" emblem, side by side. The emblem
 * is mandatory wherever the project presents itself (hub footer, "How it's made") and
 * is never smaller than the GAIK logo beside it: both share one height. The files are used
 * as they are, never cropped or recoloured.
 */
export function Funding({ className }: { className?: string }) {
  return (
    <div className={cn("flex flex-wrap items-center gap-x-8 gap-y-4", className)}>
      <img
        src="/brand/gaik_logo_fullwhite.svg"
        alt="GAIK, Generative AI-Enhanced Knowledge Management in Business"
        width={1376}
        height={702}
        loading="lazy"
        decoding="async"
        className="h-12 w-auto sm:h-14"
      />
      <img
        src="/brand/EN_Co-fundedbytheEU_RGB_NEG.png"
        alt="Co-funded by the European Union"
        width={4119}
        height={919}
        loading="lazy"
        decoding="async"
        className="h-12 w-auto sm:h-14"
      />
    </div>
  );
}
