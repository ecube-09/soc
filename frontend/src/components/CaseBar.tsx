import { useState } from "react";

import { reportUrl } from "../api/client";
import type { InvestigationResponse, RevisionSummary } from "../api/types";

interface Props {
  result: InvestigationResponse;
  revisions: RevisionSummary[];
  onOpenRevision: (revision: number) => void;
  busy: boolean;
}

/**
 * Which case and revision is on screen, who ran it, and the two clocks: the
 * incident's own time (what recency is measured against) and when it ran.
 */
export function CaseBar({ result, revisions, onOpenRevision, busy }: Props) {
  const [copied, setCopied] = useState(false);
  const latest = revisions.reduce(
    (max, r) => Math.max(max, r.revision ?? 0),
    result.revision,
  );

  const copy = async () => {
    await navigator.clipboard.writeText(result.rendered_markdown);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <section className="card card-border bg-base-100">
      <div className="card-body gap-3 p-4 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="font-mono text-sm">{result.case_id}</span>
            {revisions.length > 1 ? (
              <select
                className="select select-xs w-auto"
                value={result.revision}
                disabled={busy}
                onChange={(event) => onOpenRevision(Number(event.target.value))}
                aria-label="Revision"
              >
                {[...revisions]
                  .sort((a, b) => (b.revision ?? 0) - (a.revision ?? 0))
                  .map((r) => (
                    <option key={r.revision} value={r.revision ?? 0}>
                      Revision {r.revision} · {r.mode} ·{" "}
                      {Math.round(r.confidence * 100)}%
                    </option>
                  ))}
              </select>
            ) : (
              <span className="text-xs text-base-content/50">
                Revision {result.revision}
              </span>
            )}
            {result.revision < latest && (
              <span className="badge badge-xs badge-soft badge-warning">
                older revision
              </span>
            )}
          </div>
          <div className="join">
            <button type="button" className="btn btn-xs join-item" onClick={copy}>
              {copied ? "Copied" : "Copy Markdown"}
            </button>
            <a
              className="btn btn-xs join-item"
              href={reportUrl(result.case_id, result.revision)}
              download
            >
              Download .md
            </a>
          </div>
        </div>

        <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-3">
          <Clock label="Investigator" value={result.author ?? "unattributed"} />
          <Clock
            label="Incident time"
            value={formatTime(result.reference_time) ?? "not given; wall clock used"}
          />
          <Clock label="Report time" value={formatTime(result.created_at) ?? "—"} />
        </dl>

        {result.changes.length > 0 && result.previous && (
          <div role="status" className="alert alert-info alert-soft py-2 text-sm text-base-content">
            <ul className="grid gap-0.5">
              {result.changes.map((change) => (
                <li key={change}>{change}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

function Clock({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="text-base-content/45">{label}</dt>
      <dd className="truncate text-base-content/80">{value}</dd>
    </div>
  );
}

function formatTime(value: string | null): string | null {
  return value ? value.slice(0, 16).replace("T", " ") : null;
}
