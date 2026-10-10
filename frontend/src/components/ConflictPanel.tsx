import { useState } from "react";

import type { Conflict, Finding } from "../api/types";
import { Cite, Eyebrow, Panel } from "./bits";

interface Props {
  conflicts: Conflict[];
  onCite: (sourceId: string) => void;
  /** Absent when no case is open, e.g. an older revision is on screen. */
  onRecordFinding?: (finding: Finding) => Promise<void>;
  busy?: boolean;
}

/**
 * Past incidents that moved the same knob opposite ways. This is the one place
 * the conflict is stated; the useful output is the measurement that decides
 * which side applies, and recording it is how the conflict gets settled.
 */
export function ConflictPanel({ conflicts, onCite, onRecordFinding, busy = false }: Props) {
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
            {conflict.sides.map((side) => {
              const verdict = conflict.resolution
                ? conflict.resolution.favours_incident_id === side.incident_id
                  ? "favoured"
                  : "ruled out"
                : null;
              return (
                <div
                  key={side.incident_id}
                  className={`rounded-box border border-base-300 p-3 ${
                    verdict === "ruled out" ? "opacity-60" : "bg-base-200/40"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <Cite id={side.incident_id} onCite={onCite} />
                    {verdict && (
                      <span
                        className={`badge badge-xs ${
                          verdict === "favoured" ? "badge-soft badge-success" : "badge-ghost"
                        }`}
                      >
                        {verdict}
                      </span>
                    )}
                  </div>
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
              );
            })}
          </div>

          {conflict.resolution ? (
            <div role="status" className="alert alert-success alert-soft text-sm text-base-content">
              <span>
                Settled by measurement: {conflict.resolution.observation} Favours{" "}
                <span className="font-mono text-xs">{conflict.resolution.favours_incident_id}</span>
                {conflict.resolution.author && (
                  <span className="text-base-content/50">
                    {" "}
                    · recorded by {conflict.resolution.author}
                  </span>
                )}
              </span>
            </div>
          ) : (
            <div role="alert" className="alert alert-warning alert-soft text-sm text-base-content">
              <span>{conflict.distinguishing_question}</span>
            </div>
          )}

          {onRecordFinding && (
            <FindingForm conflict={conflict} onRecord={onRecordFinding} busy={busy} />
          )}
        </article>
      ))}
    </Panel>
  );
}

function FindingForm({
  conflict,
  onRecord,
  busy,
}: {
  conflict: Conflict;
  onRecord: (finding: Finding) => Promise<void>;
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [favours, setFavours] = useState<string>("");
  const [observation, setObservation] = useState("");
  const group = `finding-${conflict.component}-${conflict.target}`;

  if (!open) {
    return (
      <button
        type="button"
        className="btn btn-xs btn-ghost justify-self-start"
        onClick={() => setOpen(true)}
      >
        {conflict.resolution ? "Record a newer measurement" : "Record the measurement"}
      </button>
    );
  }

  const submit = async () => {
    await onRecord({
      target: conflict.target,
      component: conflict.component,
      favours_incident_id: favours,
      observation: observation.trim(),
    });
    setOpen(false);
    setObservation("");
    setFavours("");
  };

  return (
    <fieldset className="fieldset rounded-box border border-base-300 p-3">
      <legend className="fieldset-legend text-xs font-normal text-base-content/60">
        What did the measurement show?
      </legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {conflict.sides.map((side) => (
          <label key={side.incident_id} className="flex cursor-pointer items-start gap-2 text-sm">
            <input
              type="radio"
              name={group}
              className="radio radio-xs mt-0.5"
              checked={favours === side.incident_id}
              onChange={() => setFavours(side.incident_id)}
            />
            <span>
              It matches <span className="font-mono text-xs">{side.incident_id}</span>
              <span className="text-base-content/50"> ({side.direction.replaceAll("_", " ")})</span>
            </span>
          </label>
        ))}
      </div>
      <input
        className="input input-sm w-full"
        value={observation}
        onChange={(event) => setObservation(event.target.value)}
        placeholder="e.g. client pool 50/50 in use; server connected_clients 2,100 of 10,000"
      />
      <p className="label text-[11px] whitespace-normal">
        The case is re-run with this recorded. A measurement against the closest case
        lowers confidence; one for it removes the conflict penalty.
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          className="btn btn-sm btn-neutral"
          disabled={busy || !favours || !observation.trim()}
          onClick={submit}
        >
          {busy && <span className="loading loading-spinner loading-xs" />}
          Record and re-run
        </button>
        <button type="button" className="btn btn-sm btn-ghost" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </fieldset>
  );
}
