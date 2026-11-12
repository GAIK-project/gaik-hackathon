import { DEMOS, type Demo } from "@/lib/demos";

/**
 * The four cards of the hub's ring: one per demo, in dock order. Each card has its own
 * colour world: generated art (`public/art`, provenance in its README) and the colour the
 * front card casts onto the stage. The one-line text is shorter than the line of
 * `lib/demos.ts`, because a card is read at a glance.
 */
export interface HubCard {
  demo: Demo;
  /** One line on the card. */
  line: string;
  /** Generated art, 3:4, stored at twice the largest displayed size. */
  art: string;
  /** Colour the card casts as a glow, sRGB 0 to 1. */
  glow: readonly [number, number, number];
}

const DETAILS: Record<Demo["id"], Omit<HubCard, "demo">> = {
  soita: {
    line: "Puheesta valmis raportti",
    art: "/art/soita.webp",
    glow: [1, 0.44, 0.2],
  },
  studio: {
    line: "Kuvauksesta toimiva putki",
    art: "/art/studio.webp",
    glow: [0.64, 0.36, 1],
  },
  "ai-act": {
    line: "Riskiluokka ja velvoitteet",
    art: "/art/ai-act.webp",
    glow: [0.2, 0.5, 1],
  },
  tyokalut: {
    line: "Komponentit MCP-työkaluina",
    art: "/art/tyokalut.webp",
    glow: [0.1, 0.82, 0.78],
  },
};

export const HUB_CARDS: readonly HubCard[] = DEMOS.map((demo) => ({
  demo,
  ...DETAILS[demo.id],
}));

/** Width over height of a card and of its art. */
export const CARD_ASPECT = 0.75;
