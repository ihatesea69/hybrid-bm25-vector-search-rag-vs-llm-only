import { Suspense } from "react";

import { DemoClient } from "@/components/demo-client";
import { getKbSummarySnapshot } from "@/lib/backend";
import { sampleQueries } from "@/lib/demo-data";

export default async function DemoPage() {
  const kbSummary = await getKbSummarySnapshot();
  return (
    <Suspense fallback={<div className="px-6 py-12 text-sm text-slate-200/70">Loading demo workbench...</div>}>
      <DemoClient initialKbSummary={kbSummary} sampleQueries={sampleQueries} />
    </Suspense>
  );
}
