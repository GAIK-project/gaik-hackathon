import type { How } from "@/lib/types";

/**
 * "Näin tämä on tehty" for Soita raportti: the intake pipeline of ARCHITECTURE.md
 * sections 8.1 and 8.2. Logo ids come from `lib/stack.ts`.
 */
export const how: How = {
  title: "Puheesta hyväksyttyyn raporttiin",
  steps: [
    {
      id: "call",
      title: "Puhelu ääniagentille",
      detail: "Agentti kirjaa kentät puheesta ja kysyy puuttuvat tiedot.",
      logo: "azure",
    },
    {
      id: "transcribe",
      title: "Ääniviesti tekstiksi",
      detail: "WhatsApp-ääniviesti tai ladattu äänite litteroidaan.",
      gaik: "Transcriber",
      logo: "whatsapp",
    },
    {
      id: "classify",
      title: "Raporttityyppi",
      detail: "Jev tunnistaa tyypin, jos sitä ei ole valittu.",
      gaik: "JevClassifier",
      logo: "typesafe",
    },
    {
      id: "extract",
      title: "Kenttien poiminta",
      detail: "Arvot poimitaan litteraatista ja jokaiselle haetaan lähde.",
      gaik: "DataExtractor, SourceTracing",
      logo: "azure",
    },
    {
      id: "urgency",
      title: "Kiireellisyys",
      detail: "Jev antaa kiireellisyydelle kalibroidun todennäköisyyden.",
      gaik: "JevClassifier",
      logo: "typesafe",
    },
    {
      id: "approve",
      title: "Word-raportti ja hyväksyntä",
      detail: "Sähköposti lähtee vasta, kun ihminen hyväksyy luonnoksen.",
      logo: "word",
    },
  ],
  stack: ["azure", "openai", "typesafe", "whatsapp", "word", "fastapi", "python", "sqlite", "csc"],
};
