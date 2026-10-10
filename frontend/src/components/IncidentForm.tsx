import { useState, type ReactNode } from "react";

import {
  ENVIRONMENTS,
  EVENT_TYPES,
  SEVERITIES,
  type CurrentIncident,
  type Example,
  type EventType,
  type Service,
  type TimelineEventInput,
} from "../api/types";

interface Props {
  incident: CurrentIncident;
  onChange: (incident: CurrentIncident) => void;
  onSubmit: () => void;
  onLoadExample: (example: Example) => void;
  examples: Example[];
  services: Service[];
  busy: boolean;
}

export function IncidentForm({
  incident,
  onChange,
  onSubmit,
  onLoadExample,
  examples,
  services,
  busy,
}: Props) {
  const [showTimeline, setShowTimeline] = useState(true);
  const set = <K extends keyof CurrentIncident>(
    key: K,
    value: CurrentIncident[K],
  ) => onChange({ ...incident, [key]: value });

  const setEvent = (index: number, patch: Partial<TimelineEventInput>) =>
    set(
      "events",
      incident.events.map((event, i) =>
        i === index ? { ...event, ...patch } : event,
      ),
    );

  const dependencies = services.find((s) => s.name === incident.service)
    ?.dependencies;

  return (
    <form
      className="card card-border flex max-h-[calc(100vh-6.5rem)] flex-col overflow-hidden bg-base-100"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <div className="card-body min-h-0 flex-1 gap-3 overflow-y-auto p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-base font-medium tracking-tight">
              Current incident
            </h1>
            <p className="mt-1 text-xs text-base-content/50">
              Symptoms, environment, and the order of events.
            </p>
          </div>
        </div>

        {examples.length > 0 && (
          <fieldset className="fieldset p-0">
            <legend className="fieldset-legend text-xs">Example</legend>
            <select
              className="select select-sm w-full"
              value=""
              onChange={(event) => {
                const found = examples.find(
                  (item) => item.case_id === event.target.value,
                );
                if (found) onLoadExample(found);
              }}
            >
              <option value="">Load a labelled case</option>
              {examples.map((example) => (
                <option key={example.case_id} value={example.case_id}>
                  {example.case_id} — {example.incident.title}
                </option>
              ))}
            </select>
          </fieldset>
        )}

        <Field label="Title">
          <input
            className="input input-sm w-full"
            value={incident.title}
            onChange={(event) => set("title", event.target.value)}
            placeholder="Payment API returning 503 after deploy"
            required
          />
        </Field>

        <Field label="What is happening">
          <textarea
            className="textarea textarea-sm w-full leading-relaxed"
            rows={4}
            value={incident.description}
            onChange={(event) => set("description", event.target.value)}
            placeholder="Write it the way you would brief someone joining the call."
          />
        </Field>

        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Service">
            <input
              className="input input-sm w-full"
              list="service-names"
              value={incident.service}
              onChange={(event) => set("service", event.target.value)}
              required
            />
            <datalist id="service-names">
              {services.map((service) => (
                <option key={service.name} value={service.name}>
                  {service.team}
                </option>
              ))}
            </datalist>
          </Field>
          <Field label="Environment">
            <select
              className="select select-sm w-full"
              value={incident.environment}
              onChange={(event) => set("environment", event.target.value)}
            >
              {ENVIRONMENTS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Severity">
            <select
              className="select select-sm w-full"
              value={incident.severity}
              onChange={(event) => set("severity", event.target.value)}
            >
              {SEVERITIES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </Field>
        </div>

        {dependencies && (
          <p className="text-xs leading-relaxed text-base-content/55">
            {incident.service} depends on {dependencies.join(", ")}. Cases on
            those services are considered too.
          </p>
        )}

        <Field label="Symptoms" hint="One per line">
          <textarea
            className="textarea textarea-sm w-full"
            rows={4}
            value={incident.symptoms.join("\n")}
            onChange={(event) => set("symptoms", splitLines(event.target.value))}
            placeholder={"HTTP 503 rate 34%\nredis pool checkout timeouts"}
          />
        </Field>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Suspected dependencies">
            <input
              className="input input-sm w-full"
              value={incident.suspected_dependencies.join(", ")}
              onChange={(event) =>
                set("suspected_dependencies", splitCommas(event.target.value))
              }
              placeholder="redis-cache"
            />
          </Field>
          <Field label="Region">
            <input
              className="input input-sm w-full"
              value={incident.region ?? ""}
              onChange={(event) => set("region", event.target.value || null)}
              placeholder="ap-south-1"
            />
          </Field>
        </div>

        <Field label="Stack">
          <input
            className="input input-sm w-full"
            value={incident.tech_context.join(", ")}
            onChange={(event) =>
              set("tech_context", splitCommas(event.target.value))
            }
            placeholder="Redis 7.2, payment-api v2.8.1"
          />
        </Field>

        <div className="mt-1 flex items-center justify-between">
          <h2 className="text-[11px] font-medium uppercase tracking-[0.16em] text-base-content/45">
            Timeline
          </h2>
          <div className="flex gap-1">
            <button
              type="button"
              className="btn btn-xs btn-ghost"
              onClick={() => setShowTimeline((value) => !value)}
            >
              {showTimeline ? "Hide" : "Show"}
            </button>
            <button
              type="button"
              className="btn btn-xs btn-ghost"
              onClick={() =>
                set("events", [...incident.events, blankEvent(incident)])
              }
            >
              Add event
            </button>
          </div>
        </div>

        {incident.events.length === 0 && (
          <p className="text-xs leading-relaxed text-base-content/55">
            No events yet. Without a timeline, cases are matched on text and
            metadata only, and confidence is reduced.
          </p>
        )}

        {showTimeline && incident.events.length > 0 && (
          <ol className="grid gap-2">
            {incident.events.map((event, index) => (
              <li
                key={index}
                className="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-2"
              >
                <span className="pt-2 font-mono text-[10px] tabular-nums text-base-content/35">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <div className="grid min-w-0 gap-2 rounded-box border border-base-300 bg-base-200/50 p-2">
                  <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
                    <input
                      className="input input-xs w-full min-w-0"
                      type="datetime-local"
                      value={event.at}
                      onChange={(e) => setEvent(index, { at: e.target.value })}
                    />
                    <button
                      type="button"
                      className="btn btn-xs btn-ghost btn-square"
                      onClick={() =>
                        set(
                          "events",
                          incident.events.filter((_, i) => i !== index),
                        )
                      }
                      aria-label="Remove event"
                    >
                      ×
                    </button>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <select
                      className="select select-xs w-full min-w-0"
                      value={event.type}
                      onChange={(e) =>
                        setEvent(index, { type: e.target.value as EventType })
                      }
                    >
                      {EVENT_TYPES.map((type) => (
                        <option key={type} value={type}>
                          {type.replaceAll("_", " ")}
                        </option>
                      ))}
                    </select>
                    <input
                      className="input input-xs w-full min-w-0"
                      value={event.service}
                      onChange={(e) =>
                        setEvent(index, { service: e.target.value })
                      }
                      placeholder="service"
                    />
                  </div>
                  <input
                    className="input input-xs w-full min-w-0"
                    value={event.description}
                    onChange={(e) =>
                      setEvent(index, { description: e.target.value })
                    }
                    placeholder="What happened"
                  />
                </div>
              </li>
            ))}
          </ol>
        )}

        <Field label="Log excerpt" hint="Optional">
          <textarea
            className="textarea textarea-sm w-full font-mono text-xs leading-relaxed"
            rows={4}
            value={incident.log_excerpt ?? ""}
            onChange={(event) => set("log_excerpt", event.target.value || null)}
            placeholder="Paste the lines around the onset."
          />
        </Field>

      </div>
      <div className="shrink-0 border-t border-base-300 bg-base-100 px-5 py-3">
        <button type="submit" className="btn btn-primary w-full" disabled={busy}>
          {busy && <span className="loading loading-spinner loading-xs" />}
          {busy ? "Investigating" : "Investigate"}
        </button>
        <p className="mt-2 text-center text-[11px] text-base-content/40">
          Proposes what to check. Does not change anything.
        </p>
      </div>
    </form>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <fieldset className="fieldset p-0">
      <legend className="fieldset-legend text-xs font-normal text-base-content/60">
        {label}
        {hint && <span className="ml-2 text-base-content/35">{hint}</span>}
      </legend>
      {children}
    </fieldset>
  );
}

function blankEvent(incident: CurrentIncident): TimelineEventInput {
  const last = incident.events[incident.events.length - 1];
  return {
    at: last?.at ?? localNow(),
    type: "error_spike",
    service: incident.service,
    description: "",
  };
}

function localNow(): string {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  return now.toISOString().slice(0, 16);
}

function splitLines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function splitCommas(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}
