import { useState } from "react";

import { extractTimeline } from "../api/client";
import type { ProposedEvent, TimelineEventInput, TimelineExtraction } from "../api/types";

interface Props {
  logText: string;
  service: string;
  /** Date for log lines that carry only a clock time. */
  date: string | null;
  onAccept: (events: TimelineEventInput[]) => void;
}

/**
 * Rules read the pasted log and propose events. Nothing joins the timeline
 * until the engineer ticks it, because a mis-typed event changes the ranking.
 */
export function TimelineProposals({ logText, service, date, onAccept }: Props) {
  const [extraction, setExtraction] = useState<TimelineExtraction | null>(null);
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const propose = async () => {
    setBusy(true);
    setError(null);
    try {
      const found = await extractTimeline(logText, service, date);
      setExtraction(found);
      setChosen(new Set(found.events.map((_, i) => i)));
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : "Could not read the log");
    } finally {
      setBusy(false);
    }
  };

  const accept = () => {
    if (!extraction) return;
    onAccept(extraction.events.filter((_, i) => chosen.has(i)).map(toInput));
    setExtraction(null);
  };

  const toggle = (index: number) =>
    setChosen((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });

  return (
    <div className="grid gap-2">
      <button
        type="button"
        className="btn btn-xs btn-ghost justify-self-start"
        disabled={busy || !logText.trim() || !service.trim()}
        onClick={propose}
      >
        {busy && <span className="loading loading-spinner loading-xs" />}
        Propose timeline events from the log
      </button>

      {error && <p className="text-xs text-error">{error}</p>}

      {extraction && (
        <div className="grid gap-2 rounded-box border border-base-300 bg-base-200/50 p-2">
          <p className="text-[11px] leading-relaxed text-base-content/55">
            {extraction.events.length} proposed from {extraction.lines_read} lines
            {extraction.lines_without_time > 0 &&
              `; ${extraction.lines_without_time} had no timestamp`}
            {extraction.lines_unclassified > 0 &&
              `; ${extraction.lines_unclassified} matched no rule`}
            . Untick anything wrong.
          </p>
          {extraction.events.length > 0 && (
            <ul className="grid gap-1.5">
              {extraction.events.map((event, index) => (
                <Proposal
                  key={`${event.at}-${index}`}
                  event={event}
                  checked={chosen.has(index)}
                  onToggle={() => toggle(index)}
                />
              ))}
            </ul>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              className="btn btn-xs"
              disabled={chosen.size === 0}
              onClick={accept}
            >
              Add {chosen.size} to timeline
            </button>
            <button
              type="button"
              className="btn btn-xs btn-ghost"
              onClick={() => setExtraction(null)}
            >
              Discard
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Proposal({
  event,
  checked,
  onToggle,
}: {
  event: ProposedEvent;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <li>
      <label className="flex cursor-pointer items-start gap-2">
        <input
          type="checkbox"
          className="checkbox checkbox-xs mt-0.5"
          checked={checked}
          onChange={onToggle}
        />
        <span className="min-w-0 text-xs">
          <span className="font-mono tabular-nums text-base-content/60">
            {event.at.slice(11, 16)}
          </span>{" "}
          <span className="badge badge-xs badge-ghost">{event.type.replaceAll("_", " ")}</span>{" "}
          <span className="text-base-content/70">{event.service}</span>
          <span className="block truncate font-mono text-[11px] text-base-content/45">
            {event.source_line}
          </span>
        </span>
      </label>
    </li>
  );
}

function toInput(event: ProposedEvent): TimelineEventInput {
  return {
    at: event.at.slice(0, 16),
    type: event.type,
    service: event.service,
    description: event.description,
  };
}
