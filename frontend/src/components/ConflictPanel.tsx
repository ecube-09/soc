import type { Conflict } from "../api/types";
import { Cite, Eyebrow, Panel } from "./bits";

/**
 * Past incidents that moved the same knob opposite ways. The useful output is
 * the measurement that decides which side applies.
 */
export function ConflictPanel({
  conflicts,
  onCite,
}: {
  conflicts: Conflict[];
  onCite: (sourceId: string) => void;
}) {
  if (conflicts.length === 0) return null;

  return (
    <Panel>
      <Eyebrow>Conflicting historical guidance</Eyebrow>
      {conflicts.map((conflict) => (
        <article key={`${conflict.component}-${conflict.target}`} className="grid gap-3">
          <p className="text-sm">
            <span className="font-mono text-xs">{conflict.target}</span>
            <span className="text-base-content/45"> on </span>
            <span className="font-mono text-xs">{conflict.component}</span>
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            {conflict.sides.map((side) => (
              <div
                key={side.incident_id}
                className="rounded-box border border-base-300 bg-base-200/40 p-3"
              >
                <Cite id={side.incident_id} onCite={onCite} />
                <p className="mt-2 text-sm leading-relaxed">
                  <span className="badge badge-xs badge-ghost mr-2">
                    {side.direction.replaceAll("_", " ")}
                  </span>
                  {side.action}
                </p>
                <p className="mt-2 text-xs leading-relaxed text-base-content/55">
                  {side.root_cause}
                </p>
              </div>
            ))}
          </div>
          <div
            role="alert"
            className="alert alert-warning alert-soft text-sm text-base-content"
          >
            <span>{conflict.distinguishing_question}</span>
          </div>
        </article>
      ))}
    </Panel>
  );
}
