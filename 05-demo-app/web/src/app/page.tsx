import Link from "next/link";

import { FailureCaseList } from "@/components/failure-case-list";
import { MetricCard } from "@/components/metric-card";
import { PhaseOverview } from "@/components/phase-overview";
import { QueryExamples } from "@/components/query-examples";
import { StatusPill } from "@/components/status-pill";
import { getDemoPhasesSnapshot, getDemoSummarySnapshot, getHealthSnapshot, getKbSummarySnapshot } from "@/lib/backend";
import { sampleQueries } from "@/lib/demo-data";

function formatMetric(value: number | undefined, digits = 3) {
  return typeof value === "number" ? value.toFixed(digits) : "N/A";
}

export default async function HomePage() {
  const [health, kbSummary, demoSummary, phaseSnapshot] = await Promise.all([
    getHealthSnapshot(),
    getKbSummarySnapshot(),
    getDemoSummarySnapshot(),
    getDemoPhasesSnapshot(),
  ]);

  const retrieval = demoSummary.summary.retrieval_hybrid;
  const hybridRag = demoSummary.summary.answer_hybrid_rag;
  const llmOnly = demoSummary.summary.answer_llm_only;
  const pairwise = demoSummary.summary.pairwise_hybrid_rag_vs_llm_only;

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-7xl flex-col gap-10 px-6 py-8 md:px-10 md:py-12">
      <section className="overflow-hidden rounded-[40px] border border-white/10 bg-slate-950/50 p-8 shadow-[0_30px_120px_rgba(15,23,42,0.42)] md:p-10">
        <div className="grid gap-10 lg:grid-cols-[1.1fr_0.9fr]">
          <div>
            <StatusPill ok={health.ok} label={health.ok ? "System ready" : "System degraded"} />
            <p className="mt-6 text-sm font-semibold uppercase tracking-[0.32em] text-cyan-100/64">MedIR benchmark demo</p>
            <h1 className="mt-4 max-w-4xl text-5xl font-semibold tracking-tight text-white md:text-7xl">
              Biến pipeline IR/RAG thành một demo app đủ sắc để thuyết trình.
            </h1>
            <p className="mt-6 max-w-3xl text-base leading-8 text-slate-200/72">
              Lớp demo này trình bày cùng lúc retrieval evidence, grounded answer và closed-book answer trên knowledge base nutrition-health hiện có. Đây là demo phục vụ học thuật, không thay thế tư vấn y khoa chuyên môn.
            </p>

            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/demo" className="rounded-full bg-cyan-300 px-5 py-3 text-sm font-semibold text-slate-950 transition hover:bg-cyan-200">
                Mở live demo
              </Link>
              <span className="rounded-full border border-white/10 bg-white/[0.05] px-5 py-3 text-sm text-slate-200/75">
                {kbSummary.documents} docs indexed · {kbSummary.embeddedNodes} nodes embedded
              </span>
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <MetricCard eyebrow="Retrieval recall@10" value={formatMetric(retrieval?.["recall@10"])} note="Hiệu quả thu hồi trên batch đánh giá gần nhất." />
            <MetricCard eyebrow="Retrieval mrr@10" value={formatMetric(retrieval?.["mrr@10"])} note="Xếp hạng tài liệu liên quan đầu tiên." />
            <MetricCard eyebrow="Hybrid RAG correctness" value={formatMetric(hybridRag?.correctness, 2)} note="Điểm judge cho câu trả lời grounded." />
            <MetricCard eyebrow="Pairwise win rate" value={formatMetric(pairwise?.hybrid_rag_win_rate, 2)} note="Tỉ lệ Hybrid RAG thắng LLM-only ở pairwise compare." />
          </div>
        </div>
      </section>

      <section className="grid gap-5 md:grid-cols-3">
        <MetricCard eyebrow="BM25 + Vector" value={kbSummary.availableModes.join(" · ")} note="Các mode hiện có của phase 3 runtime." />
        <MetricCard eyebrow="Faithfulness" value={formatMetric(hybridRag?.faithfulness, 2)} note="Judge score của grounded answer trên evidence hiện có." />
        <MetricCard eyebrow="LLM-only relevancy" value={formatMetric(llmOnly?.relevancy, 2)} note="Mức liên quan khi trả lời không dùng retrieval context." />
      </section>

      <section className="rounded-[32px] border border-white/10 bg-white/[0.045] p-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.28em] text-cyan-100/64">Pipeline breakdown</p>
            <h2 className="mt-3 text-3xl font-semibold text-white">Chi tiết từng phase của hệ thống</h2>
          </div>
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-4 py-2 text-sm text-slate-200/72">
            Snapshot at {new Date(phaseSnapshot.updatedAt).toLocaleString("vi-VN")}
          </span>
        </div>
        <div className="mt-6">
          <PhaseOverview phases={phaseSnapshot.phases} />
        </div>
      </section>

      <section className="grid gap-8 lg:grid-cols-[1.1fr_0.9fr]">
        <section className="rounded-[32px] border border-white/10 bg-white/[0.045] p-6">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.28em] text-cyan-100/64">Demo prompts</p>
              <h2 className="mt-3 text-3xl font-semibold text-white">Bộ câu hỏi mẫu để kể câu chuyện benchmark</h2>
            </div>
            <Link href="/demo" className="text-sm text-cyan-100/75 transition hover:text-cyan-50">
              Sang workbench
            </Link>
          </div>
          <div className="mt-6">
            <QueryExamples queries={sampleQueries} />
          </div>
        </section>

        <section className="rounded-[32px] border border-white/10 bg-slate-950/48 p-6">
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-cyan-100/64">Operational snapshot</p>
          <h2 className="mt-3 text-3xl font-semibold text-white">Artifacts và backend readiness</h2>
          <div className="mt-6 grid gap-3">
            {Object.entries(kbSummary.artifacts).map(([key, value]) => (
              <div key={key} className="flex items-center justify-between rounded-2xl border border-white/8 bg-white/[0.04] px-4 py-3">
                <span className="text-sm text-slate-100">{key}</span>
                <StatusPill ok={value} label={value ? "ready" : "missing"} />
              </div>
            ))}
          </div>
        </section>
      </section>

      <section className="grid gap-6 lg:grid-cols-2">
        <FailureCaseList title="Retrieval Hybrid" rows={demoSummary.failure_cases.retrieval_hybrid ?? []} />
        <FailureCaseList title="Hybrid RAG" rows={demoSummary.failure_cases.answer_hybrid_rag ?? []} />
      </section>
    </main>
  );
}
