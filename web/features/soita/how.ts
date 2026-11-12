import type { How } from "@/lib/types";

/**
 * "How this is built" for Soita raportti: the call relay of `server/app/voice` and the
 * intake pipeline of `server/app/reports/pipeline.py` (ARCHITECTURE.md sections 8.1, 8.2,
 * 15.6 and 15.7). Logo ids come from `lib/stack.ts`.
 */
export const how: How = {
  title: "From speech to an approved report",
  steps: [
    {
      id: "call",
      title: "A call to the voice agent",
      detail:
        "The browser streams 24 kHz audio over a WebSocket. A realtime model on Azure (Sweden) records fields as it hears them and asks for what is missing.",
      logo: "azure",
    },
    {
      id: "transcribe",
      title: "Voice notes become text",
      detail: "An uploaded recording or a WhatsApp voice note is converted with ffmpeg and transcribed.",
      gaik: "Transcriber",
      logo: "whatsapp",
    },
    {
      id: "classify",
      title: "Report type",
      detail: "When no type was chosen, a classifier on Azure picks it. Spoken content never leaves the EU.",
      gaik: "classifier",
      logo: "azure",
    },
    {
      id: "extract",
      title: "Fields with evidence",
      detail: "Values are extracted from the transcript, each with the words it rests on.",
      gaik: "DataExtractor, SourceTracing",
      logo: "azure",
    },
    {
      id: "urgency",
      title: "Urgency",
      detail: "The same classifier rates the report urgent, normal or not urgent, with a probability.",
      gaik: "classifier",
      logo: "azure",
    },
    {
      id: "document",
      title: "Word report",
      detail: "The fields are written into a Word file on the server.",
      logo: "word",
    },
    {
      id: "approve",
      title: "A person approves",
      detail: "The email is a fixed template with the Word file attached. Nothing is sent before someone approves it.",
      logo: "gmail",
    },
  ],
  stack: ["azure", "openai", "whatsapp", "word", "fastapi", "python", "csc"],
};
