import { StatusPill } from "@/components/status-pill";
import type { PhaseDetail } from "@/lib/types";

type PhaseOverviewProps = {
  phases: PhaseDetail[];
  compact?: boolean;
};

function isReady(status: PhaseDetail["status"]) {
  return status === "ready";
}

export function PhaseOverview({ phases, compact = false }: PhaseOverviewProps) {
  if (!phases.length) {
    return (
      <section className="rounded-[28px] border border-dashed border-white/12 bg-white/[0.03] px-6 py-10 text-sm text-slate-200/70">
        Chưa có phase snapshot nào từ backend.
      </section>
    );
  }

  return (
    <section className="grid gap-5 xl:grid-cols-2">
      {phases.map((phase) => (
        <article key={phase.id} className="rounded-[28px] border border-white/10 bg-slate-950/48 p-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.28em] text-cyan-100/64">{phase.id.toUpperCase()}</p>
              <h3 className="mt-2 text-2xl font-semibold text-white">{phase.title}</h3>
            </div>
            <StatusPill ok={isReady(phase.status)} label={phase.status} />
          </div>

          <p className="mt-4 text-sm leading-7 text-slate-200/76">{phase.summary}</p>

          <div className={`mt-5 grid gap-3 ${compact ? "sm:grid-cols-2" : "md:grid-cols-2"}`}>
            {phase.stats.map((stat) => (
              <div key={`${phase.id}-${stat.label}`} className="rounded-2xl border border-white/8 bg-white/[0.04] px-4 py-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-400">{stat.label}</p>
                <p className="mt-2 text-xl font-semibold text-white">{stat.value}</p>
              </div>
            ))}
          </div>

          <div className="mt-5">
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-cyan-100/64">Outputs</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {phase.outputs.map((output) => (
                <span key={`${phase.id}-${output}`} className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs text-slate-200/74">
                  {output}
                </span>
              ))}
            </div>
          </div>

          <div className="mt-5 space-y-3">
            {phase.details.map((detail) => (
              <div key={`${phase.id}-${detail}`} className="rounded-2xl border border-white/8 bg-white/[0.035] px-4 py-3 text-sm leading-6 text-slate-200/74">
                {detail}
              </div>
            ))}
          </div>
        </article>
      ))}
    </section>
  );
}
