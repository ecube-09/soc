import type { ReactNode } from "react";

export function Cite({
  id,
  onCite,
}: {
  id: string;
  onCite: (sourceId: string) => void;
}) {
  return (
    <button
      type="button"
      className="btn btn-xs btn-ghost h-6 min-h-0 px-1 font-mono text-[11px] font-normal text-base-content/70 underline decoration-base-300 underline-offset-2 hover:decoration-base-content"
      onClick={() => onCite(id)}
    >
      {id}
    </button>
  );
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <h2 className="text-[11px] font-medium uppercase tracking-[0.16em] text-base-content/45">
      {children}
    </h2>
  );
}

export function Panel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card card-border bg-base-100 ${className}`}>
      <div className="card-body gap-4 p-5 sm:p-6">{children}</div>
    </section>
  );
}
