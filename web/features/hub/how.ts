import type { How } from "@/lib/types";

/**
 * "Näin tämä on tehty" for the whole app: the runtime of ARCHITECTURE.md sections 2 and
 * 12. Logo ids come from `lib/stack.ts`.
 */
export const how: How = {
  title: "Yksi sovellus yhdessä kontissa",
  steps: [
    {
      id: "web",
      title: "Selainsovellus",
      detail: "Next.js-sovellus viedään staattisiksi tiedostoiksi.",
      logo: "nextjs",
    },
    {
      id: "api",
      title: "Rajapinta",
      detail: "FastAPI tarjoilee sivut, rajapinnan ja MCP-päätteen yhdestä prosessista.",
      logo: "fastapi",
    },
    {
      id: "gaik",
      title: "gaik-komponentit",
      detail: "Demot kutsuvat PyPI:ssä julkaistua gaik-kirjastoa.",
      gaik: "gaik",
      logo: "python",
    },
    {
      id: "models",
      title: "Mallit",
      detail: "Kieli, puhe ja litterointi Azure OpenAI:sta, tyypitetyt päätökset Jeviltä.",
      logo: "azure",
    },
    {
      id: "data",
      title: "Tallennus",
      detail: "Raportit ovat SQLite-kannassa ja tiedostot levyllä.",
      logo: "sqlite",
    },
    {
      id: "host",
      title: "Ajoympäristö",
      detail: "Kontti pyörii CSC:n Rahti-palvelussa.",
      logo: "csc",
    },
  ],
  stack: [
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
    "azure",
    "openai",
    "typesafe",
    "mcp",
    "sqlite",
    "docker",
    "openshift",
    "csc",
  ],
};
