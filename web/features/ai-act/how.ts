import type { How } from "@/lib/types";

/**
 * "Näin tämä on tehty" for the AI Act checker: the method of ARCHITECTURE.md section 8.4.
 * Logo ids come from `lib/stack.ts`. The retrieval component is the one section 13 names
 * for search without a database; the demo's owner corrects it if the build differs.
 */
export const how: How = {
  title: "Kuvauksesta riskiluokkaan ja velvoitteisiin",
  steps: [
    {
      id: "retrieve",
      title: "Säännösten haku",
      detail: "Sopivat säännökset haetaan asetuksen voimassa olevasta tekstistä.",
      gaik: "Embedder",
      logo: "azure",
    },
    {
      id: "classify",
      title: "Riskiluokka",
      detail: "Jev luokittelee järjestelmän neljään riskiluokkaan.",
      gaik: "JevClassifier",
      logo: "typesafe",
    },
    {
      id: "cite",
      title: "Säännös ja lainaus",
      detail: "Kielimalli valitsee soveltuvan säännöksen ja lainaa sitä.",
      logo: "azure",
    },
    {
      id: "verify",
      title: "Lainauksen tarkistus",
      detail: "Jokainen lainaus etsitään säännöksen tekstistä koodilla.",
      logo: "python",
    },
    {
      id: "obligations",
      title: "Velvoitteet",
      detail: "Riskiluokka yhdistetään tarkistettuun velvoitetaulukkoon.",
    },
    {
      id: "review",
      title: "Ihmisen tarkistus",
      detail: "Eriävä tai epävarma tulos merkitään ihmisen tarkistettavaksi.",
    },
  ],
  stack: ["azure", "openai", "typesafe", "python", "fastapi"],
};
