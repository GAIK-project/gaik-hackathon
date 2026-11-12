import type { How } from "@/lib/types";

/**
 * "Näin tämä on tehty" for GAIK Studio: generate, run and export of ARCHITECTURE.md
 * section 8.3. Logo ids come from `lib/stack.ts`.
 */
export const how: How = {
  title: "Kuvauksesta ajettavaan putkeen",
  steps: [
    {
      id: "describe",
      title: "Kuvaus käyttötapauksesta",
      detail: "Malli valitsee yhden neljästä pohjasta ja täyttää määrittelyn.",
      logo: "azure",
    },
    {
      id: "validate",
      title: "Määrittelyn tarkistus",
      detail: "Palvelin validoi määrittelyn. Vapaata koodia malli ei kirjoita.",
      logo: "fastapi",
    },
    {
      id: "schema",
      title: "Kentät vaatimuksesta",
      detail: "Poimittavat kentät syntyvät selkokielisestä vaatimuksesta.",
      gaik: "SchemaGenerator",
      logo: "azure",
    },
    {
      id: "run",
      title: "Ajo oikeilla komponenteilla",
      detail: "Putki ajetaan näytteillä tai omilla tiedostoilla.",
      gaik: "DataExtractor, Transcriber, JevClassifier",
      logo: "python",
    },
    {
      id: "export",
      title: "Vienti Python-koodiksi",
      detail: "Sama putki ajettavana skriptinä ja blueprint-tiedostona.",
      logo: "python",
    },
  ],
  stack: ["azure", "openai", "typesafe", "python", "fastapi"],
};
