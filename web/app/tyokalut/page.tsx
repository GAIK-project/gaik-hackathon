import type { Metadata } from "next";

import { PageShell } from "@/components/shell/page-shell";
import { how } from "@/features/tyokalut/how";
import { TyokalutScreen } from "@/features/tyokalut/screen";

const TITLE = "Tools for agents";

export const metadata: Metadata = { title: TITLE };

export default function Page() {
  return (
    <PageShell title={TITLE} how={how}>
      <TyokalutScreen />
    </PageShell>
  );
}
