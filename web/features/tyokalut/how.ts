import type { How } from "@/lib/types";

/**
 * "Näin tämä on tehty" for Työkalut agentille: the MCP server of ARCHITECTURE.md
 * section 8.5. Logo ids come from `lib/stack.ts`.
 */
export const how: How = {
  title: "Samat komponentit agentin työkaluina",
  steps: [
    {
      id: "agent",
      title: "Agentti liittyy palvelimeen",
      detail: "Valmis agentti, esimerkiksi Claude, saa osoitteen ja avaimen.",
      logo: "claude",
    },
    {
      id: "mcp",
      title: "MCP-palvelin",
      detail: "Työkalut tarjotaan Streamable HTTP -yhteydellä FastAPI-sovelluksesta.",
      logo: "mcp",
    },
    {
      id: "tool",
      title: "Työkalu kutsuu komponenttia",
      detail: "Työn tekee sama gaik-komponentti kuin demoissa.",
      gaik: "DataExtractor, Transcriber, JevClassifier",
      logo: "python",
    },
    {
      id: "log",
      title: "Kutsu näkyy lokissa",
      detail: "Jokainen kutsu näkyy sivulla, kun se alkaa ja kun se päättyy.",
      logo: "fastapi",
    },
  ],
  stack: ["mcp", "claude", "fastapi", "python", "azure", "typesafe"],
};
