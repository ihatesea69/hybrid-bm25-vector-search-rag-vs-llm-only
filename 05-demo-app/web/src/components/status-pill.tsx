type StatusPillProps = {
  ok: boolean;
  label: string;
};

export function StatusPill({ ok, label }: StatusPillProps) {
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-[0.24em] ${
        ok
          ? "border-emerald-400/40 bg-emerald-500/15 text-emerald-100"
          : "border-amber-300/40 bg-amber-400/15 text-amber-100"
      }`}
    >
      <span className={`h-2 w-2 rounded-full ${ok ? "bg-emerald-300" : "bg-amber-200"}`} />
      {label}
    </span>
  );
}
