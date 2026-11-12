import type { How } from "@/lib/types";

/**
 * How the AI Act checker works, step by step as `server/app/aiact/assess.py` runs it. Two
 * methods answer at the same time (the classifier and the provision pick); code checks the
 * quotes and joins the two answers. Logo ids come from `lib/stack.ts`.
 */
export const how: How = {
  title: "From a description to a risk tier and its obligations",
  steps: [
    {
      id: "retrieve",
      title: "Search the regulation",
      detail:
        "The description is embedded on Azure OpenAI and matched against the paragraphs and points of the consolidated AI Act.",
      gaik: "Embedder",
      logo: "azure",
    },
    {
      id: "classify",
      title: "Estimate the risk tier",
      detail:
        "Jev gives a calibrated probability for each of the four tiers. This step runs in the United States, on at most 2,000 characters.",
      gaik: "JevClassifier",
      logo: "typesafe",
    },
    {
      id: "provision",
      title: "Pick the deciding provision",
      detail:
        "At the same time a chat model on Azure OpenAI reads the candidate provisions, picks the one that decides the tier and quotes it.",
      logo: "azure",
    },
    {
      id: "verify",
      title: "Check the quotes",
      detail:
        "Code looks for every quoted passage in the provision's own text. A quote that is not there is replaced by the provision's opening words.",
      logo: "python",
    },
    {
      id: "obligations",
      title: "Map the obligations",
      detail:
        "The tier is joined to a reviewed table of obligations, each with its article and the date it applies from.",
      logo: "python",
    },
    {
      id: "review",
      title: "Flag for a person",
      detail:
        "When the two methods disagree, the estimate is uncertain or a quote could not be verified, the result asks for human review.",
    },
  ],
  stack: ["azure", "openai", "typesafe", "python", "fastapi"],
};
