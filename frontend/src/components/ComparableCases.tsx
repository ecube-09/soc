import { useState, type ReactNode } from "react";

import type { Candidate, ExcludedCase } from "../api/types";
import { Cite, Eyebrow, Panel } from "./bits";
import { TimelineCompare } from "./TimelineCompare";

interface Props {
  candidates: Candidate[];
  excluded: ExcludedCase[];
  regionPolicy: "prefer" | "require" | null;
  onCite: (sourceId: string) => void;
}

const COMPONENT_LABELS: Record<string, string> = {
  semantic: "text",
  metadata: "metadata",
  timeline_sequence: "sequence",
  timeline_interval: "intervals",
  recency: "recency",
  service_graph: "service map",
};

export function ComparableCases({ candidates, excluded, regionPolicy, onCite }: Props) {
  const [openId, setOpenId] = useState<string | null>(
    candidates[0]?.incident_id ?? null,
  );

  return (
    <Panel>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <Eyebrow>Comparable cases</Eyebrow>
        <span className="text-[11px] text-base-content/45">
          Environment must match.{" "}
          {regionPolicy === "require"
            ? "Region must match."
            : regionPolicy === "prefer"
              ? "Other regions are kept and flagged."
              : ""}
        </span>
      </div>
      {candidates.length === 0 ? (
        <p className="text-sm text-base-content/60">
          No historical case matched this service and environment.
        </p>
      ) : (
        <div className="divide-y divide-base-300 border-y border-base-300">
          {candidates.map((candidate) => {
            const open = openId === candidate.incident_id;
            return (
              <article key={candidate.incident_id}>
                <button
                  type="button"
                  className="flex w-full items-baseline gap-3 py-3 text-left"
                  onClick={() =>
                    setOpenId(open ? null : candidate.incident_id)
                  }
                >
                  <span className="w-10 shrink-0 font-mono text-xs tabular-nums text-base-content/70">
                    {percent(candidate.score)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="font-mono text-xs text-base-content/45">
                      {candidate.incident_id}
                    </span>
                    <span className="mt-0.5 block truncate text-sm">
                      {candidate.title}
                    </span>
                  </span>
                  <span className="hidden shrink-0 gap-1 sm:flex">
                    {candidate.supports_hypothesis && (
                      <span className="badge badge-xs badge-soft badge-info">supports</span>
                    )}
                    {candidate.flags.map((flag) => (
                      <span
                        key={flag}
                        className={`badge badge-xs ${
                          flag === "stale" || flag === "other-region"
                            ? "badge-soft badge-warning"
                            : "badge-ghost"
                        }`}
                      >
                        {flag}
                      </span>
                    ))}
                  </span>
                </button>

                {open && (
                  <div className="grid gap-4 pb-5 pl-[3.25rem] pr-1">
                    {candidate.flags.includes("stale") && (
                      <div role="alert" className="alert alert-warning alert-soft text-sm text-base-content">
                        <span>
                          {candidate.age_days} days old
                          {candidate.tech_context.length > 0 &&
                            `, on ${candidate.tech_context.join(", ")}`}
                          . The reasoning may transfer. The specific remediation
                          values probably do not.
                        </span>
                      </div>
                    )}

                    <dl className="grid gap-3 text-sm sm:grid-cols-3">
                      <Fact label="Service">
                        {candidate.service} · {candidate.environment}
                      </Fact>
                      <Fact label="When">
                        {candidate.started_at.replace("T", " ")}
                        {candidate.duration_minutes !== null &&
                          ` · ${candidate.duration_minutes} min`}
                      </Fact>
                      <Fact label="Severity">{candidate.severity}</Fact>
                    </dl>

                    {candidate.supports_hypothesis && candidate.support_reason && (
                      <p className="text-sm leading-relaxed text-base-content/80">
                        <span className="text-base-content/45">Counts as support: </span>
                        {candidate.support_reason}
                      </p>
                    )}

                    <div>
                      <Eyebrow>Why it matched</Eyebrow>
                      <ul className="mt-2 grid gap-1 text-sm text-base-content/75">
                        {candidate.metadata_reasons.map((reason) => (
                          <li key={reason}>{reason}</li>
                        ))}
                        {candidate.graph_reason && <li>{candidate.graph_reason}</li>}
                      </ul>
                      <div className="mt-3 grid gap-2 sm:grid-cols-2">
                        {Object.entries(candidate.components).map(([key, value]) => (
                          <div key={key} className="grid gap-1">
                            <div className="flex justify-between text-[11px] text-base-content/45">
                              <span>{COMPONENT_LABELS[key] ?? key}</span>
                              <span className="tabular-nums">{percent(value)}</span>
                            </div>
                            <progress
                              className="progress progress-neutral h-1"
                              value={Math.round(value * 100)}
                              max={100}
                            />
                          </div>
                        ))}
                      </div>
                    </div>

                    <div>
                      <Eyebrow>Timeline alignment</Eyebrow>
                      <div className="mt-2">
                        <TimelineCompare
                          alignment={candidate.timeline}
                          historicalId={candidate.incident_id}
                        />
                      </div>
                    </div>

                    <div className="max-w-prose text-sm leading-relaxed">
                      <Eyebrow>Root cause</Eyebrow>
                      <p className="mt-2 text-base-content/80">{candidate.root_cause}</p>
                      <Eyebrow>Resolution</Eyebrow>
                      <p className="mt-2 text-base-content/80">{candidate.resolution}</p>
                    </div>

                    {candidate.actions.length > 0 && (
                      <ul className="grid gap-1 font-mono text-xs text-base-content/60">
                        {candidate.actions.map((action) => (
                          <li key={action}>{action}</li>
                        ))}
                      </ul>
                    )}

                    <div className="flex flex-wrap gap-1">
                      {candidate.citation_ids.map((id) => (
                        <Cite key={id} id={id} onCite={onCite} />
                      ))}
                    </div>
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}
      <ExcludedList excluded={excluded} />
    </Panel>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-[0.14em] text-base-content/40">
        {label}
      </dt>
      <dd className="mt-1 text-sm">{children}</dd>
    </div>
  );
}

function ExcludedList({ excluded }: { excluded: ExcludedCase[] }) {
  if (excluded.length === 0) return null;

  const byFilter = (["environment", "region"] as const)
    .map((filter) => [filter, excluded.filter((e) => e.filter === filter).length] as const)
    .filter(([, count]) => count > 0)
    .map(([filter, count]) => `${count} by ${filter}`)
    .join(", ");

  return (
    <details className="text-sm">
      <summary className="cursor-pointer text-xs text-base-content/45">
        {excluded.length === 1 ? "1 case filtered out" : `${excluded.length} cases filtered out`}{" "}
        ({byFilter})
      </summary>
      <ul className="mt-2 grid gap-1 text-sm text-base-content/65">
        {excluded.map((item) => (
          <li key={item.incident_id}>
            <span className="badge badge-xs badge-ghost mr-2">{item.filter}</span>
            <span className="font-mono text-xs">{item.incident_id}</span>{" "}
            {item.title}
            <span className="text-base-content/40"> — {item.reason}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}
