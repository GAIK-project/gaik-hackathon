import type { Metadata } from "next";

import { PageShell } from "@/components/shell/page-shell";
import { how } from "@/features/ai-act/how";
import { AiActScreen } from "@/features/ai-act/screen";

const TITLE = "AI Act -tarkastaja";

export const metadata: Metadata = { title: TITLE };

export default function Page() {
  return (
    <PageShell title={TITLE} how={how}>
      <AiActScreen />
    </PageShell>
  );
}
