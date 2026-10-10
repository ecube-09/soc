import type { NormalizedEvent, TimelineAlignment } from "../api/types";

/**
 * Both timelines drawn as offsets from their own anchor. daisyUI's timeline
 * shows one sequence; this comparison needs two tracks on the same scale, so
 * it is built from utilities.
 */
export function TimelineCompare({
  alignment,
  historicalId,
}: {
  alignment: TimelineAlignment;
  historicalId: string;
}) {
  if (alignment.current.length === 0 || alignment.historical.length === 0) {
    return (
      <p className="text-sm text-base-content/50">No timeline to compare.</p>
    );
  }

  const shared = new Set(alignment.matched.map((pair) => pair.type));
  const span = Math.max(
    ...alignment.current.map((event) => event.offset_minutes),
    ...alignment.historical.map((event) => event.offset_minutes),
    1,
  );

  return (
    <div>
      <p className="text-xs text-base-content/50">
        Anchored on {alignment.anchor}. Sequence{" "}
        {percent(alignment.sequence_score)}, intervals{" "}
        {percent(alignment.interval_score)}. Dark marks are event types on both
        timelines.
      </p>
      <Track label="Now" events={alignment.current} shared={shared} span={span} />
      <Track
        label={historicalId}
        events={alignment.historical}
        shared={shared}
        span={span}
      />
      {alignment.matched.length > 0 && (
        <ul className="mt-2 grid gap-1 text-xs text-base-content/55">
          {alignment.matched.map((pair, index) => (
            <li key={`${pair.type}-${index}`}>
              <span className="font-mono">{pair.type}</span> +{pair.current_offset}
              m now, +{pair.historical_offset}m then
              {pair.drift_minutes > 0 && ` · ${pair.drift_minutes}m apart`}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Track({
  label,
  events,
  shared,
  span,
}: {
  label: string;
  events: NormalizedEvent[];
  shared: Set<string>;
  span: number;
}) {
  return (
    <div className="my-3 grid grid-cols-[4.25rem_minmax(0,1fr)] items-center gap-3">
      <span className="truncate font-mono text-[11px] text-base-content/45">
        {label}
      </span>
      <div className="relative h-px bg-base-300">
        {events.map((event, index) => {
          const matched = shared.has(event.type);
          return (
            <span
              key={index}
              className={`absolute top-1/2 size-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full ${
                matched ? "bg-neutral" : "bg-base-content/25"
              }`}
              style={{ left: `${(event.offset_minutes / span) * 100}%` }}
              title={`+${event.offset_minutes}m ${event.type.replaceAll("_", " ")} [${event.service}] ${event.description}`}
            />
          );
        })}
      </div>
    </div>
  );
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}
