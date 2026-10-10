import type { InvestigationResponse, Step } from "../api/types";
import { Cite, Eyebrow, Panel } from "./bits";

interface Props {
  result: InvestigationResponse;
  onCite: (sourceId: string) => void;
}

export function Recommendation({ result, onCite }: Props) {
  const escalation = result.escalation_report;

  return (
    <Panel>
      <div className="flex items-center justify-between gap-3">
        <Eyebrow>
          {escalation ? "Escalation report" : "Recommended investigation"}
        </Eyebrow>
        <span
          className={`badge badge-sm ${escalation ? "badge-soft badge-warning" : "badge-ghost"}`}
        >
          {result.mode}
        </span>
      </div>

      <div className="grid gap-3 border border-base-300 px-4 py-3 sm:grid-cols-[7.5rem_minmax(0,1fr)] sm:gap-0">
        <div>
          <p className="text-xs text-base-content/45">Confidence</p>
          <p className="mt-1 text-2xl font-medium tabular-nums tracking-tight">
            {Math.round(result.confidence * 100)}%
          </p>
          <p className="mt-1 text-xs text-base-content/50">
            Root cause {result.root_cause_status}
          </p>
        </div>
        <div className="min-w-0 sm:border-l sm:border-base-300 sm:pl-4">
          <p className="text-xs text-base-content/45">Why this mode</p>
          <p className="mt-1 text-sm leading-relaxed text-base-content/70">
            {result.gate_reason}
          </p>
        </div>
      </div>

      {escalation ? (
        <EscalationBody report={escalation} onCite={onCite} />
      ) : (
        <InvestigationBody result={result} onCite={onCite} />
      )}

      {result.validation_notes.length > 0 && (
        <details className="collapse collapse-arrow border border-base-300 bg-base-200/50">
          <summary className="collapse-title min-h-0 py-2 text-xs text-base-content/55">
            {result.validation_notes.length} validation note
            {result.validation_notes.length > 1 ? "s" : ""}
          </summary>
          <div className="collapse-content text-sm">
            <ul className="list">
              {result.validation_notes.map((note) => (
                <li className="list-row px-0 text-sm" key={note}>
                  {note}
                </li>
              ))}
            </ul>
          </div>
        </details>
      )}

      <p className="border-t border-base-300 pt-3 text-xs text-base-content/45">
        {escalation
          ? "No remediation has been applied. This report states what is known and what is not."
          : "Nothing here has been applied. An engineer decides and acts."}
      </p>
    </Panel>
  );
}

function InvestigationBody({ result, onCite }: Props) {
  return (
    <div className="grid gap-4">
      <p className="max-w-prose text-[15px] leading-relaxed">{result.headline}</p>
      {result.root_cause_hypothesis && (
        <div className="border-l-2 border-neutral py-1 pl-4">
          <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-base-content/45">
            Hypothesis
          </p>
          <p className="mt-1 max-w-prose text-sm leading-relaxed">
            {result.root_cause_hypothesis}
          </p>
        </div>
      )}

      <ol className="grid gap-4">
        {result.steps.map((step, index) => (
          <StepItem key={index} index={index} step={step} onCite={onCite} />
        ))}
      </ol>

      {result.cautions.length > 0 && (
        <div role="alert" className="alert alert-warning alert-soft items-start text-base-content">
          <div>
            <p className="text-xs font-medium">Cautions</p>
            <ul className="mt-1 list-disc pl-4 text-sm leading-relaxed">
              {result.cautions.map((caution) => (
                <li key={caution}>{caution}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {result.unresolved.length > 0 && <PlainList title="Unresolved" items={result.unresolved} />}
    </div>
  );
}

function EscalationBody({
  report,
  onCite,
}: {
  report: NonNullable<InvestigationResponse["escalation_report"]>;
  onCite: (sourceId: string) => void;
}) {
  return (
    <div className="grid gap-4">
      <p className="max-w-prose text-[15px] leading-relaxed">
        {report.incident_title}. The root cause is undetermined from the
        available evidence.
      </p>

      <dl className="grid gap-3 border border-base-300 px-4 py-3 sm:grid-cols-3">
        <div>
          <dt className="text-xs text-base-content/45">Severity</dt>
          <dd className="mt-1 text-sm">{report.severity}</dd>
        </div>
        <div>
          <dt className="text-xs text-base-content/45">Service</dt>
          <dd className="mt-1 text-sm">
            {report.service}
            <span className="text-base-content/45"> · {report.environment}</span>
          </dd>
        </div>
        <div>
          <dt className="text-xs text-base-content/45">Requested team</dt>
          <dd className="mt-1 text-sm">{report.requested_team}</dd>
        </div>
      </dl>

      <PlainList title="Observed symptoms" items={report.observed_symptoms} />
      <PlainList title="Timeline" items={report.timeline} mono />
      <PlainList title="Historical correlation" items={report.historical_correlation} />
      <PlainList title="Evidence gathered" items={report.evidence_gathered} />

      {report.investigations_considered.length > 0 && (
        <div>
          <Eyebrow>Investigations considered</Eyebrow>
          <ol className="mt-3 grid gap-4">
            {report.investigations_considered.map((step, index) => (
              <StepItem key={index} index={index} step={step} onCite={onCite} />
            ))}
          </ol>
        </div>
      )}

      <PlainList title="Unresolved" items={report.unresolved} />
      <PlainList
        title="Recommended next investigation"
        items={report.recommended_next_investigation}
      />

      {report.citations.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {report.citations.map((id) => (
            <Cite key={id} id={id} onCite={onCite} />
          ))}
        </div>
      )}
    </div>
  );
}

function StepItem({
  index,
  step,
  onCite,
}: {
  index: number;
  step: Step;
  onCite: (sourceId: string) => void;
}) {
  return (
    <li className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-x-3">
      <span className="pt-0.5 font-mono text-xs text-base-content/35">
        {String(index + 1).padStart(2, "0")}
      </span>
      <div>
        <p className="text-sm leading-relaxed">{step.action}</p>
        {step.rationale && (
          <p className="mt-1 text-sm leading-relaxed text-base-content/55">
            {step.rationale}
          </p>
        )}
        <div className="mt-1.5 flex flex-wrap gap-1">
          {step.citations.map((id) => (
            <Cite key={id} id={id} onCite={onCite} />
          ))}
        </div>
      </div>
    </li>
  );
}

function PlainList({
  title,
  items,
  mono = false,
}: {
  title: string;
  items: string[];
  mono?: boolean;
}) {
  return (
    <div>
      <Eyebrow>{title}</Eyebrow>
      <ul className={`mt-2 grid gap-1.5 ${mono ? "font-mono text-xs" : "text-sm"}`}>
        {items.map((item) => (
          <li key={item} className="leading-relaxed text-base-content/75">
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}
