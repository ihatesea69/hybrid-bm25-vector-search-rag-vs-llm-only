type FailureCaseListProps = {
  title: string;
  rows: Array<Record<string, unknown>>;
};

export function FailureCaseList({ title, rows }: FailureCaseListProps) {
  return (
    <section className="rounded-[28px] border border-white/10 bg-slate-950/45 p-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-cyan-100/70">Failure cases</p>
          <h3 className="mt-2 text-xl font-semibold text-white">{title}</h3>
        </div>
        <span className="rounded-full bg-white/8 px-3 py-1 text-xs text-slate-200">{rows.length} mục</span>
      </div>
      <div className="mt-5 space-y-3">
        {rows.length ? (
          rows.slice(0, 3).map((row, index) => (
            <article key={`${title}-${index}`} className="rounded-2xl border border-white/8 bg-white/[0.04] p-4">
              <p className="text-sm font-medium text-white">
                {(row.query_text as string | undefined) ?? (row.query_id as string | undefined) ?? "Unknown query"}
              </p>
              <p className="mt-2 text-sm leading-6 text-slate-200/70">
                {JSON.stringify(row).slice(0, 180)}
                {JSON.stringify(row).length > 180 ? "..." : ""}
              </p>
            </article>
          ))
        ) : (
          <p className="rounded-2xl border border-dashed border-white/12 px-4 py-6 text-sm text-slate-200/70">
            Chưa có failure case được ghi nhận trong artifact hiện tại.
          </p>
        )}
      </div>
    </section>
  );
}
