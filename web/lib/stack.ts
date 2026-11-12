/**
 * Stack logos in `public/stack/`. Official marks from svgl and Iconify (fetched 12.11.2026),
 * the CSC mark from csc.fi; never drawn, recoloured or cropped here.
 *
 * Where a mark comes in two variants, `file` is the one made for dark surfaces
 * (`*.dark.svg`). `plate` marks a logo that exists only for light backgrounds: StackLogo
 * shows it unchanged on a small light plate so it stays readable on the dark stage.
 *
 * Render through `<StackLogo id="…" />`, never by inlining the SVG.
 */

export interface StackEntry {
  id: string;
  title: string;
  /** File name in `public/stack/`. */
  file: string;
  /** Show on a light plate: the mark has no variant for dark surfaces. */
  plate?: boolean;
}

const entries = [
  // Models and services
  { id: "azure", title: "Microsoft Azure", file: "azure.svg" },
  { id: "openai", title: "OpenAI", file: "openai.dark.svg" },
  { id: "typesafe", title: "TypeSafe AI", file: "typesafe.dark.svg" },
  { id: "claude", title: "Claude", file: "claude.svg" },
  { id: "grok", title: "Grok", file: "grok.dark.svg" },
  { id: "huggingface", title: "Hugging Face", file: "huggingface.svg" },
  { id: "mcp", title: "Model Context Protocol", file: "mcp.dark.svg" },
  // Channels and documents
  { id: "whatsapp", title: "WhatsApp", file: "whatsapp.svg" },
  { id: "meta", title: "Meta", file: "meta.svg" },
  { id: "gmail", title: "Gmail", file: "gmail.svg" },
  { id: "word", title: "Microsoft Word", file: "word.svg" },
  { id: "pdf", title: "PDF", file: "pdf.svg" },
  // Front end
  { id: "nextjs", title: "Next.js", file: "nextjs.svg" },
  { id: "react", title: "React", file: "react.dark.svg" },
  { id: "tailwindcss", title: "Tailwind CSS", file: "tailwindcss.svg" },
  { id: "shadcn-ui", title: "shadcn/ui", file: "shadcn-ui.dark.svg" },
  { id: "motion", title: "Motion", file: "motion.dark.svg" },
  { id: "threejs", title: "Three.js", file: "threejs.dark.svg" },
  { id: "bun", title: "Bun", file: "bun.svg" },
  // Back end and data
  { id: "fastapi", title: "FastAPI", file: "fastapi.svg" },
  { id: "python", title: "Python", file: "python.svg" },
  { id: "uv", title: "uv", file: "uv.svg" },
  { id: "sqlite", title: "SQLite", file: "sqlite.svg", plate: true },
  { id: "postgresql", title: "PostgreSQL", file: "postgresql.svg" },
  // Hosting
  { id: "docker", title: "Docker", file: "docker.svg" },
  { id: "openshift", title: "OpenShift", file: "openshift.svg" },
  { id: "cloudflare", title: "Cloudflare", file: "cloudflare.svg" },
  { id: "csc", title: "CSC", file: "csc.svg", plate: true },
] as const satisfies readonly StackEntry[];

export type StackId = (typeof entries)[number]["id"];

export const STACK: readonly StackEntry[] = entries;

const byId = new Map<string, StackEntry>(entries.map((entry) => [entry.id, entry]));

/** The entry for a logo id, or undefined when the id is unknown. */
export function stackEntry(id: string): StackEntry | undefined {
  return byId.get(id);
}

/** Public URL of a logo file. */
export function stackSrc(entry: StackEntry): string {
  return `/stack/${entry.file}`;
}
