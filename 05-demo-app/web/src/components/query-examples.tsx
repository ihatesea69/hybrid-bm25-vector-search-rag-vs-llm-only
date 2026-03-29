import Link from "next/link";

type QueryExamplesProps = {
  queries: string[];
};

export function QueryExamples({ queries }: QueryExamplesProps) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {queries.map((query) => (
        <Link
          key={query}
          href={`/demo?q=${encodeURIComponent(query)}`}
          className="rounded-[24px] border border-white/10 bg-white/[0.045] p-4 text-left transition hover:-translate-y-0.5 hover:border-cyan-300/40 hover:bg-white/[0.08]"
        >
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-cyan-100/60">Prompt demo</p>
          <p className="mt-3 text-sm leading-6 text-slate-100">{query}</p>
        </Link>
      ))}
    </div>
  );
}
