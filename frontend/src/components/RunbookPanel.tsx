import type { RunbookMatch } from "../api/types";
import { Cite, Eyebrow, Panel } from "./bits";

export function RunbookPanel({
  runbooks,
  onCite,
}: {
  runbooks: RunbookMatch[];
  onCite: (sourceId: string) => void;
}) {
  return (
    <Panel>
      <Eyebrow>Runbooks</Eyebrow>
      {runbooks.length === 0 ? (
        <p className="text-sm text-base-content/60">
          No runbook trigger matches these symptoms, so there is no documented
          procedure to follow here.
        </p>
      ) : (
        <div className="grid gap-5">
          {runbooks.map((match) => (
            <article key={match.runbook_id}>
              <div className="flex items-baseline justify-between gap-3">
                <div className="min-w-0">
                  <Cite id={match.runbook_id} onCite={onCite} />
                  <p className="mt-1 text-sm">{match.title}</p>
                </div>
                <span className="font-mono text-xs tabular-nums text-base-content/45">
                  {Math.round(match.score * 100)}%
                </span>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-base-content/50">
                Trigger: {match.trigger.replace(/\.$/, "")}. {match.reason}. v{match.version}, updated{" "}
                {match.updated_at.slice(0, 10)}.
              </p>
              <ol className="mt-3 grid gap-1.5 pl-4 text-sm leading-relaxed text-base-content/80">
                {match.steps.map((step) => (
                  <li key={step} className="list-decimal">
                    {step}
                  </li>
                ))}
              </ol>
              {match.escalate_to && (
                <p className="mt-2 text-xs text-base-content/45">
                  Escalates to {match.escalate_to}.
                </p>
              )}
            </article>
          ))}
        </div>
      )}
    </Panel>
  );
}
