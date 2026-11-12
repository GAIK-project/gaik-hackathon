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
    label: "Call",
    title: "Call in a report",
    line: "Tell the voice agent what you saw. The report fills in from speech and is sent once a person approves it.",
    icon: Phone,
  },
  {
    id: "studio",
    href: "/studio",
    label: "Studio",
    title: "GAIK Studio",
    line: "Describe a use case. You get a pipeline of gaik components and watch it run.",
    icon: Workflow,
  },
  {
    id: "ai-act",
    href: "/ai-act",
    label: "AI Act",
    title: "AI Act checker",
    line: "Describe an AI system. You see its risk tier, the obligations and the articles behind them.",
    icon: Scale,
  },
  {
    id: "tyokalut",
    href: "/tyokalut",
    label: "Tools",
    title: "Tools for agents",
    line: "The same components as MCP tools for ready-made agents, with every call shown in a log.",
    icon: Wrench,
  },
];

export function demoById(id: Demo["id"]): Demo {
  const demo = DEMOS.find((item) => item.id === id);
  if (!demo) throw new Error(`Unknown demo: ${id}`);
  return demo;
}
