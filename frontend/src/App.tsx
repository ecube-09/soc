import { useCallback, useEffect, useState } from "react";

import { fetchExamples, fetchServices, investigate } from "./api/client";
import type {
  CurrentIncident,
  Example,
  InvestigationResponse,
  Service,
} from "./api/types";
import { Eyebrow, Panel } from "./components/bits";
import { ComparableCases } from "./components/ComparableCases";
import { ConflictPanel } from "./components/ConflictPanel";
import { IncidentForm } from "./components/IncidentForm";
import { Recommendation } from "./components/Recommendation";
import { RunbookPanel } from "./components/RunbookPanel";
import { RuntimeStatus } from "./components/RuntimeStatus";
import { SourcePanel } from "./components/SourcePanel";

const EMPTY: CurrentIncident = {
  title: "",
  description: "",
  service: "",
  environment: "production",
  severity: "SEV-3",
  region: null,
  started_at: null,
  symptoms: [],
  suspected_dependencies: [],
  tech_context: [],
  events: [],
  log_excerpt: null,
};

export default function App() {
  const [incident, setIncident] = useState<CurrentIncident>(EMPTY);
  const [examples, setExamples] = useState<Example[]>([]);
  const [services, setServices] = useState<Service[]>([]);
  const [result, setResult] = useState<InvestigationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sourceId, setSourceId] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchExamples(controller.signal).then(setExamples).catch(() => undefined);
    fetchServices(controller.signal).then(setServices).catch(() => undefined);
    return () => controller.abort();
  }, []);

  const submit = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await investigate(incident));
      setSourceId(null);
    } catch (caught: unknown) {
      setError(
        caught instanceof Error ? caught.message : "The investigation failed",
      );
    } finally {
      setBusy(false);
    }
  }, [incident]);

  const loadExample = useCallback((example: Example) => {
    setIncident({
      ...example.incident,
      events: example.incident.events.map((event) => ({
        ...event,
        at: event.at.slice(0, 16),
      })),
    });
    setResult(null);
    setError(null);
  }, []);

  return (
    <div className="min-h-screen bg-base-200 text-base-content">
      <div className="navbar min-h-14 border-b border-base-300 bg-base-100 px-4 sm:px-6">
        <div className="navbar-start gap-3">
          <span className="text-sm font-medium tracking-tight">
            Incident desk
          </span>
          <span className="hidden text-sm text-base-content/50 md:inline">
            Past cases and runbooks. Nothing here applies a fix.
          </span>
        </div>
        <div className="navbar-end">
          <RuntimeStatus />
        </div>
      </div>

      <main
        className={`mx-auto grid max-w-[1500px] items-start gap-5 px-4 py-5 sm:px-6 lg:grid-cols-[minmax(0,34rem)_minmax(0,1fr)] ${
          sourceId ? "xl:grid-cols-[minmax(0,30rem)_minmax(0,1fr)_20rem]" : ""
        }`}
      >
        <div className="min-w-0 lg:sticky lg:top-5">
          <IncidentForm
            incident={incident}
            onChange={setIncident}
            onSubmit={submit}
            onLoadExample={loadExample}
            examples={examples}
            services={services}
            busy={busy}
          />
        </div>

        <div className="grid min-w-0 gap-4">
          {error && (
            <div role="alert" className="alert alert-error alert-soft">
              <span>{error}</span>
            </div>
          )}

          {!result && !error && (
            <Panel>
              <Eyebrow>Waiting</Eyebrow>
              <p className="max-w-prose text-sm leading-relaxed text-base-content/70">
                Describe the incident, or load one of the {examples.length}{" "}
                examples. The result is a cited investigation, or an escalation
                report when the evidence does not support a cause.
              </p>
            </Panel>
          )}

          {result && (
            <>
              <Recommendation result={result} onCite={setSourceId} />
              <ConflictPanel
                conflicts={result.evidence.conflicts}
                onCite={setSourceId}
              />
              <ComparableCases
                candidates={result.evidence.candidates}
                excluded={result.evidence.excluded}
                onCite={setSourceId}
              />
              <RunbookPanel
                runbooks={result.evidence.runbooks}
                onCite={setSourceId}
              />
              <p className="px-1 text-xs text-base-content/45">
                Case <span className="font-mono">{result.case_id}</span>. Later
                uploads attach here. Packet captures and file samples are not
                analysed in this version.
              </p>
            </>
          )}
        </div>

        {sourceId && (
          <SourcePanel sourceId={sourceId} onClose={() => setSourceId(null)} />
        )}
      </main>
    </div>
  );
}
