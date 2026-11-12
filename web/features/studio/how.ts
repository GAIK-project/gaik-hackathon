import type { How } from "@/lib/types";

/**
 * How GAIK Studio is made, as `server/app/studio/` does it: `generate.py` (the spec),
 * `catalog.py` (the four fixed pipelines) and the export of ARCHITECTURE.md section 15.9.
 * Logo ids come from `lib/stack.ts`.
 */
export const how: How = {
  title: "From a description to a pipeline that runs",
  steps: [
    {
      id: "spec",
      title: "The request becomes a spec",
      detail:
        "One structured chat call on Azure OpenAI picks one of four templates and writes the title, the summary and the labels or example questions. It is checked in code, repaired once, then refused.",
      gaik: "get_llm_config",
      logo: "azure",
    },
    {
      id: "fields",
      title: "Fields from plain words",
      detail:
        "For tables and reports the fields come from the requirement. The pipeline itself is fixed per template: the model never writes steps or code.",
      gaik: "SchemaGenerator",
      logo: "fastapi",
    },
    {
      id: "table",
      title: "Documents to a table",
      detail: "PDF, Word and text files are read locally, then the fields are filled on Azure OpenAI, one row per document.",
      gaik: "PyMuPDFParser, DocxParser, DataExtractor",
      logo: "pdf",
    },
    {
      id: "report",
      title: "A recording to a report",
      detail:
        "ffmpeg prepares the sound, Azure OpenAI writes it out and fills the fields with the words each rests on, and python-docx writes the Word file.",
      gaik: "Transcriber, DataExtractor",
      logo: "word",
    },
    {
      id: "answers",
      title: "Questions to documents",
      detail:
        "Pages and the question become vectors, the closest pages are picked, and one answer is written from them. Every quote is checked against its page.",
      gaik: "Embedder",
      logo: "azure",
    },
    {
      id: "labels",
      title: "Documents to labels",
      detail:
        "Jev gives every label a probability. This step runs at TypeSafe in the United States, on the first 2,000 characters of each text.",
      gaik: "JevClassifier",
      logo: "typesafe",
    },
    {
      id: "export",
      title: "Export as code",
      detail:
        "A Python script from a fixed template per pipeline. The spec sits in it as one data literal and is read at run time, never inserted into code.",
      logo: "python",
    },
  ],
  stack: ["azure", "openai", "typesafe", "python", "fastapi"],
};
