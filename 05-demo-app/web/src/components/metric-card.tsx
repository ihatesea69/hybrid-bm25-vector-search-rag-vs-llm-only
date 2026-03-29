type MetricCardProps = {
  eyebrow: string;
  value: string;
  note: string;
};

export function MetricCard({ eyebrow, value, note }: MetricCardProps) {
  return (
    <article className="rounded-[28px] border border-white/10 bg-white/[0.06] p-5 shadow-[0_24px_80px_rgba(15,23,42,0.28)] backdrop-blur">
      <p className="text-xs font-semibold uppercase tracking-[0.28em] text-cyan-100/70">{eyebrow}</p>
      <p className="mt-4 text-3xl font-semibold text-white">{value}</p>
      <p className="mt-2 text-sm leading-6 text-slate-200/70">{note}</p>
    </article>
  );
}
