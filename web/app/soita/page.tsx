import type { Metadata } from "next";

import { PageShell } from "@/components/shell/page-shell";
import { how } from "@/features/soita/how";
import { SoitaScreen } from "@/features/soita/screen";

const TITLE = "Soita raportti";

export const metadata: Metadata = { title: TITLE };

export default function Page() {
  return (
    <PageShell title={TITLE} how={how}>
      <SoitaScreen />
    </PageShell>
  );
}
