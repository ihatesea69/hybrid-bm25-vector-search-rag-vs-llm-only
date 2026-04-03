"use client";

import Link from "next/link";
import { useDeferredValue, useEffect, useEffectEvent, useRef, useState, useTransition } from "react";
import { useSearchParams } from "next/navigation";

import { PhaseOverview } from "@/components/phase-overview";
import { StatusPill } from "@/components/status-pill";
import type { Citation, DemoQueryPayload, DemoResultRow, KbSummary, PhaseDetail } from "@/lib/types";

type DemoClientProps = {
  initialKbSummary: KbSummary;
  initialPhases: PhaseDetail[];
  sampleQueries: string[];
};

function CitationRow({ citation }: { citation: Citation }) {
  return (
    <div className="rounded-2xl border border-white/8 bg-white/[0.03] px-4 py-3 text-sm text-slate-100">
      <span className="font-semibold text-cyan-100">[{citation.citationId}]</span> {citation.title}
      <div className="mt-2 text-xs uppercase tracking-[0.22em] text-slate-400">{citation.docId ?? "unknown-doc"}</div>
    </div>
  );
}

function EvidenceCard({ row, index }: { row: DemoResultRow; index: number }) {
  const rerankerModel =
    typeof row.rerankerMeta?.model === "string" ? row.rerankerMeta.model : undefined;
  const bm25Rank =
    typeof row.bm25Meta?.rerankRank === "number"
      ? row.bm25Meta.rerankRank
      : typeof row.bm25Meta?.originalRank === "number"
        ? row.bm25Meta.originalRank
        : undefined;
  const vectorRank =
    typeof row.vectorMeta?.rerankRank === "number"
      ? row.vectorMeta.rerankRank
      : typeof row.vectorMeta?.originalRank === "number"
        ? row.vectorMeta.originalRank
        : undefined;
  return (
    <article className="rounded-[24px] border border-white/10 bg-slate-950/45 p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-cyan-100/60">Evidence #{index + 1}</p>
          <h3 className="mt-2 text-lg font-semibold text-white">{row.title}</h3>
          <p className="mt-1 text-xs uppercase tracking-[0.22em] text-slate-400">{row.docId ?? "unknown-doc"}</p>
        </div>
        <div className="rounded-full border border-cyan-300/30 bg-cyan-400/10 px-3 py-1 text-sm font-semibold text-cyan-100">
          {row.score.toFixed(3)}
        </div>
      </div>
      <p className="mt-4 text-sm leading-7 text-slate-200/78">{row.snippet || "No snippet available."}</p>
      <div className="mt-4 flex flex-wrap gap-2 text-xs text-slate-300/72">
        {row.retrievalPath ? (
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1">Path: {row.retrievalPath}</span>
        ) : null}
        {typeof bm25Rank === "number" ? (
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1">BM25 rank: {bm25Rank}</span>
        ) : null}
        {typeof vectorRank === "number" ? (
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1">Vector rank: {vectorRank}</span>
        ) : null}
        {rerankerModel ? (
          <span className="rounded-full border border-cyan-300/20 bg-cyan-400/[0.07] px-3 py-1 text-cyan-100">
            Reranker: {rerankerModel}
          </span>
        ) : null}
      </div>
    </article>
  );
}

function AnswerPanel({
  title,
  mode,
  answerText,
  accent,
}: {
  title: string;
  mode: string;
  answerText: string;
  accent: "cyan" | "amber";
}) {
  const accentClasses =
    accent === "cyan"
      ? "border-cyan-300/20 bg-cyan-400/[0.06] text-cyan-100"
      : "border-amber-300/20 bg-amber-400/[0.06] text-amber-100";

  return (
    <section className="rounded-[28px] border border-white/10 bg-slate-950/55 p-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-400">Answer mode</p>
          <h2 className="mt-2 text-2xl font-semibold text-white">{title}</h2>
        </div>
        <span className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-[0.24em] ${accentClasses}`}>
          {mode}
        </span>
      </div>
      <p className="mt-5 whitespace-pre-wrap text-sm leading-7 text-slate-100/88">{answerText}</p>
    </section>
  );
}

export function DemoClient({ initialKbSummary, initialPhases, sampleQueries }: DemoClientProps) {
  const searchParams = useSearchParams();
  const seededQuery = searchParams.get("q") ?? sampleQueries[0] ?? "";
  const autorun = searchParams.get("autorun") === "1";
  const [queryText, setQueryText] = useState(seededQuery);
  const [topK, setTopK] = useState(5);
  const [result, setResult] = useState<DemoQueryPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isPending, startTransition] = useTransition();
  const deferredQuery = useDeferredValue(queryText);
  const hasAutoRun = useRef(false);

  async function submitQuery(nextQuery?: string) {
    const query = (nextQuery ?? queryText).trim();
    if (!query) {
      setError("Hãy nhập một câu hỏi để chạy demo.");
      return;
    }

    setIsSubmitting(true);
    setError(null);

    try {
      const response = await fetch("/api/demo/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ queryText: query, topK }),
      });

      const payload = (await response.json()) as DemoQueryPayload & { error?: string };
      if (!response.ok) {
        throw new Error(payload.error ?? "Demo backend không phản hồi.");
      }

      startTransition(() => {
        setQueryText(query);
        setResult(payload);
      });
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Không thể gọi demo backend.");
    } finally {
      setIsSubmitting(false);
    }
  }

  const runSeededQuery = useEffectEvent(() => {
    void submitQuery(seededQuery);
  });

  useEffect(() => {
    if (!autorun || hasAutoRun.current) {
      return;
    }
    hasAutoRun.current = true;
    runSeededQuery();
  }, [autorun]);

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-8 px-6 py-12 md:px-10">
      <header className="rounded-[36px] border border-white/10 bg-slate-950/55 p-8 shadow-[0_30px_120px_rgba(15,23,42,0.45)]">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-3xl">
            <StatusPill ok={initialKbSummary.status === "ok"} label={initialKbSummary.status === "ok" ? "KB ready" : "Backend degraded"} />
            <p className="mt-5 text-sm font-semibold uppercase tracking-[0.28em] text-cyan-100/65">Live retrieval workbench</p>
            <h1 className="mt-4 text-4xl font-semibold tracking-tight text-white md:text-6xl">
              Trình diễn trực tiếp Hybrid RAG so với LLM-only.
            </h1>
            <p className="mt-5 max-w-2xl text-base leading-8 text-slate-200/74">
              Demo này chạy trực tiếp trên knowledge base đã index của đồ án. Mọi câu trả lời chỉ phục vụ minh họa học thuật, không thay thế tư vấn y khoa chuyên môn.
            </p>
          </div>
          <div className="grid min-w-[260px] gap-3 text-sm text-slate-200/76">
            <div className="rounded-2xl border border-white/8 bg-white/[0.04] px-4 py-3">
              <p className="text-xs uppercase tracking-[0.22em] text-slate-400">Indexed docs</p>
              <p className="mt-2 text-2xl font-semibold text-white">{initialKbSummary.documents}</p>
            </div>
            <div className="rounded-2xl border border-white/8 bg-white/[0.04] px-4 py-3">
              <p className="text-xs uppercase tracking-[0.22em] text-slate-400">Modes</p>
              <p className="mt-2 text-sm text-white">{initialKbSummary.availableModes.join(" · ")}</p>
            </div>
          </div>
        </div>
      </header>

      <section className="grid gap-8 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="rounded-[32px] border border-white/10 bg-white/[0.045] p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-cyan-100/65">Câu hỏi hiện tại</p>
              <h2 className="mt-2 text-2xl font-semibold text-white">{deferredQuery || "Chọn một prompt mẫu hoặc nhập câu hỏi mới"}</h2>
            </div>
            <Link href="/" className="text-sm text-cyan-100/80 transition hover:text-cyan-50">
              Quay về dashboard
            </Link>
          </div>

          <div className="mt-6 space-y-4">
            <label className="block">
              <span className="text-sm font-medium text-slate-100">Nutrition question</span>
              <textarea
                value={queryText}
                onChange={(event) => setQueryText(event.target.value)}
                rows={4}
                className="mt-3 w-full rounded-[24px] border border-white/10 bg-slate-950/60 px-4 py-4 text-sm leading-7 text-white outline-none transition focus:border-cyan-300/50"
                placeholder="Ví dụ: Can dietary fiber help lower cholesterol?"
              />
            </label>

            <div className="flex flex-wrap items-center gap-4">
              <label className="text-sm text-slate-200/78">
                <span className="mr-3 text-xs uppercase tracking-[0.2em] text-slate-400">Top-k</span>
                <select
                  value={topK}
                  onChange={(event) => setTopK(Number(event.target.value))}
                  className="rounded-full border border-white/10 bg-slate-950 px-4 py-2 text-sm text-white outline-none"
                >
                  {[3, 5, 7, 10].map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>

              <button
                type="button"
                onClick={() => void submitQuery()}
                disabled={isSubmitting}
                className="rounded-full bg-cyan-300 px-5 py-3 text-sm font-semibold text-slate-950 transition hover:bg-cyan-200 disabled:cursor-not-allowed disabled:bg-cyan-100/60"
              >
                {isSubmitting ? "Đang chạy..." : "Chạy demo query"}
              </button>

              {isPending ? <span className="text-sm text-slate-300/70">Đang cập nhật giao diện...</span> : null}
            </div>

            {error ? (
              <div className="rounded-2xl border border-rose-300/25 bg-rose-500/10 px-4 py-3 text-sm text-rose-100">{error}</div>
            ) : null}
          </div>
        </div>

        <aside className="rounded-[32px] border border-white/10 bg-slate-950/50 p-6">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-cyan-100/65">Prompt mẫu cho buổi bảo vệ</p>
          <div className="mt-4 grid gap-3">
            {sampleQueries.map((sample) => (
              <button
                key={sample}
                type="button"
                onClick={() => void submitQuery(sample)}
                className="rounded-[22px] border border-white/8 bg-white/[0.04] px-4 py-4 text-left text-sm leading-6 text-slate-100 transition hover:border-cyan-300/40 hover:bg-white/[0.08]"
              >
                {sample}
              </button>
            ))}
          </div>
        </aside>
      </section>

      <section className="rounded-[32px] border border-white/10 bg-white/[0.045] p-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.28em] text-cyan-100/64">Pipeline breakdown</p>
            <h2 className="mt-3 text-3xl font-semibold text-white">Chi tiết từng phase ngay trong workbench</h2>
          </div>
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-4 py-2 text-sm text-slate-200/72">
            Dùng để giải thích flow khi demo live query
          </span>
        </div>
        <div className="mt-6">
          <PhaseOverview phases={initialPhases} compact />
        </div>
      </section>

      {result ? (
        <>
          <section className="grid gap-6 lg:grid-cols-2">
            <AnswerPanel title="Hybrid RAG" mode={result.hybridRag.mode} answerText={result.hybridRag.answerText} accent="cyan" />
            <AnswerPanel title="LLM-only" mode={result.llmOnly.mode} answerText={result.llmOnly.answerText} accent="amber" />
          </section>

          <section className="grid gap-8 lg:grid-cols-[1.15fr_0.85fr]">
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.24em] text-cyan-100/65">Evidence bundle</p>
                  <h2 className="mt-2 text-2xl font-semibold text-white">Top-{result.topK} retrieval results</h2>
                </div>
                <div className="rounded-full border border-white/10 bg-white/[0.04] px-4 py-2 text-sm text-slate-200">
                  batch {result.batchId}
                </div>
              </div>
              {result.hybrid.results.map((row, index) => (
                <EvidenceCard key={`${row.nodeId ?? row.docId ?? index}`} row={row} index={index} />
              ))}
            </div>

            <div className="space-y-4">
              <section className="rounded-[28px] border border-white/10 bg-white/[0.04] p-6">
                <p className="text-xs font-semibold uppercase tracking-[0.24em] text-cyan-100/65">Inline citations</p>
                <h2 className="mt-2 text-2xl font-semibold text-white">Nguồn được trích</h2>
                <div className="mt-5 space-y-3">
                  {result.hybridRag.citations.length ? (
                    result.hybridRag.citations.map((citation) => (
                      <CitationRow key={`${citation.citationId}-${citation.nodeId ?? citation.docId ?? citation.title}`} citation={citation} />
                    ))
                  ) : (
                    <p className="rounded-2xl border border-dashed border-white/12 px-4 py-6 text-sm text-slate-300/70">
                      Câu trả lời này chưa có citation nào.
                    </p>
                  )}
                </div>
              </section>

              <section className="rounded-[28px] border border-white/10 bg-white/[0.04] p-6">
                <p className="text-xs font-semibold uppercase tracking-[0.24em] text-cyan-100/65">Timing breakdown</p>
                <h2 className="mt-2 text-2xl font-semibold text-white">Độ trễ theo từng bước</h2>
                <div className="mt-5 grid gap-3">
                  {Object.entries(result.timingsMs).map(([label, value]) => (
                    <div key={label} className="rounded-2xl border border-white/8 bg-slate-950/45 px-4 py-3">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-sm font-medium uppercase tracking-[0.18em] text-slate-300">{label}</span>
                        <span className="text-base font-semibold text-white">{value.toFixed(2)} ms</span>
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            </div>
          </section>
        </>
      ) : (
        <section className="rounded-[32px] border border-dashed border-white/12 bg-slate-950/35 px-8 py-14 text-center">
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-cyan-100/60">Awaiting query</p>
          <h2 className="mt-4 text-3xl font-semibold text-white">Chưa có kết quả nào được render.</h2>
          <p className="mx-auto mt-4 max-w-2xl text-sm leading-7 text-slate-200/70">
            Hãy chọn một prompt mẫu hoặc nhập câu hỏi mới để xem đồng thời retrieval evidence, câu trả lời grounded và câu trả lời closed-book.
          </p>
        </section>
      )}
    </div>
  );
}
