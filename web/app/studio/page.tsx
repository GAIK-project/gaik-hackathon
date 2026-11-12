import type { Metadata } from "next";

import { PageShell } from "@/components/shell/page-shell";
import { how } from "@/features/studio/how";
import { StudioScreen } from "@/features/studio/screen";

const TITLE = "GAIK Studio";

export const metadata: Metadata = { title: TITLE };

export default function Page() {
  return (
    <PageShell title={TITLE} how={how}>
      <StudioScreen />
    </PageShell>
  );
}
