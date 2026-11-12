import { Phone, Scale, Workflow, Wrench, type LucideIcon } from "lucide-react";

/** The four demos, in dock order (docs/ARCHITECTURE.md section 1). */
export interface Demo {
  id: "soita" | "studio" | "ai-act" | "tyokalut";
  href: string;
  /** Short label for the dock. */
  label: string;
  /** Page title. */
  title: string;
  /** One line for the hub. */
  line: string;
  icon: LucideIcon;
}

export const DEMOS: readonly Demo[] = [
  {
    id: "soita",
    href: "/soita",
    label: "Soita",
    title: "Soita raportti",
    line: "Kerro havainto ääniagentille. Raportti täyttyy puheesta ja lähtee, kun ihminen hyväksyy sen.",
    icon: Phone,
  },
  {
    id: "studio",
    href: "/studio",
    label: "Studio",
    title: "GAIK Studio",
    line: "Kuvaile käyttötapaus. Saat putken gaik-komponenteista ja näet sen ajossa.",
    icon: Workflow,
  },
  {
    id: "ai-act",
    href: "/ai-act",
    label: "AI Act",
    title: "AI Act -tarkastaja",
    line: "Kuvaile tekoälyjärjestelmä. Näet riskiluokan, velvoitteet ja artiklat niiden takana.",
    icon: Scale,
  },
  {
    id: "tyokalut",
    href: "/tyokalut",
    label: "Työkalut",
    title: "Työkalut agentille",
    line: "Samat komponentit MCP-työkaluina valmiille agenteille, kutsut näkyvät lokissa.",
    icon: Wrench,
  },
];

export function demoById(id: Demo["id"]): Demo {
  const demo = DEMOS.find((item) => item.id === id);
  if (!demo) throw new Error(`Unknown demo: ${id}`);
  return demo;
}
