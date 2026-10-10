import { useCallback, useEffect, useMemo, useState } from "react";

import {
  addEvidence,
  fetchCase,
  fetchCases,
  fetchExamples,
  fetchRevision,
  fetchServices,
  fetchStats,
  investigate,
  rerun,
} from "./api/client";
import type {
  CaseDetail,
  CaseSummary,
  CurrentIncident,
  EvidenceKind,
  Example,
  Finding,
  InvestigationResponse,
  Service,
} from "./api/types";
import { Eyebrow, Panel } from "./components/bits";
import { CaseBar } from "./components/CaseBar";
import { CaseEvidence } from "./components/CaseEvidence";
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

const AUTHOR_KEY = "incident-desk.author";

export default function App() {
  const [incident, setIncident] = useState<CurrentIncident>(EMPTY);
  const [examples, setExamples] = useState<Example[]>([]);
  const [services, setServices] = useState<Service[]>([]);
  const [regionPolicy, setRegionPolicy] = useState<"prefer" | "require" | null>(null);
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [result, setResult] = useState<InvestigationResponse | null>(null);
  const [baseline, setBaseline] = useState<string | null>(null);
  const [author, setAuthor] = useState(() => localStorage.getItem(AUTHOR_KEY) ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sourceId, setSourceId] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchExamples(controller.signal).then(setExamples).catch(() => undefined);
    fetchServices(controller.signal).then(setServices).catch(() => undefined);
    fetchStats(controller.signal)
      .then((stats) => setRegionPolicy(stats.region_policy))
      .catch(() => undefined);
    fetchCases(controller.signal).then(setCases).catch(() => undefined);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    localStorage.setItem(AUTHOR_KEY, author);
  }, [author]);

  const investigator = author.trim() || null;
  const formEdited = result !== null && baseline !== null && incidentKey(incident) !== baseline;
  const latestRevision = detail?.revisions.reduce((m, r) => Math.max(m, r.revision ?? 0), 0) ?? 0;
  const viewingLatest = result !== null && result.revision >= latestRevision;

  const run = useCallback(async (work: () => Promise<void>, fallback: string) => {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : fallback);
    } finally {
      setBusy(false);
    }
  }, []);

  const show = useCallback(async (next: InvestigationResponse) => {
    setResult(next);
    setSourceId(null);
    const [caseDetail, recent] = await Promise.all([fetchCase(next.case_id), fetchCases()]);
    setDetail(caseDetail);
    setCases(recent);
  }, []);

  const submit = useCallback(
    () =>
      run(async () => {
        await show(await investigate(incident, investigator));
        setBaseline(incidentKey(incident));
      }, "The investigation failed"),
    [incident, investigator, run, show],
  );

  const rerunCase = useCallback(
    () =>
      run(async () => {
        if (!result) return;
        await show(await rerun(result.case_id, investigator, formEdited ? incident : undefined));
        setBaseline(incidentKey(incident));
      }, "The re-run failed"),
    [result, investigator, formEdited, incident, run, show],
  );

  const attach = useCallback(
    async (kind: EvidenceKind, payload: Record<string, unknown>) => {
      if (!result) return;
      setError(null);
      try {
        await addEvidence(result.case_id, kind, payload, investigator);
        setDetail(await fetchCase(result.case_id));
      } catch (caught: unknown) {
        setError(caught instanceof Error ? caught.message : "Could not attach that");
        throw caught;
      }
    },
    [result, investigator],
  );

  const recordFinding = useCallback(
    (finding: Finding) =>
      run(async () => {
        if (!result) return;
        await addEvidence(result.case_id, "finding", { ...finding }, investigator);
        await show(await rerun(result.case_id, investigator, formEdited ? incident : undefined));
        setBaseline(incidentKey(incident));
      }, "Could not record the measurement"),
    [result, investigator, formEdited, incident, run, show],
  );

  const openCase = useCallback(
    (caseId: string) =>
      run(async () => {
        const caseDetail = await fetchCase(caseId);
        const latest = caseDetail.revisions.reduce((m, r) => Math.max(m, r.revision ?? 0), 0);
        if (!latest) throw new Error(`${caseId} has no stored investigation yet.`);
        const opened = await fetchRevision(caseId, latest);
        const stored = [...caseDetail.artifacts].reverse().find((a) => a.kind === "incident");
        const form = stored ? toForm(stored.payload) : EMPTY;
        setIncident(form);
        setBaseline(incidentKey(form));
        setDetail(caseDetail);
        setResult(opened);
        setSourceId(null);
      }, "Could not open the case"),
    [run],
  );

  const openRevision = useCallback(
    (revision: number) =>
      run(async () => {
        if (!result) return;
        setResult(await fetchRevision(result.case_id, revision));
        setSourceId(null);
      }, "Could not open that revision"),
    [result, run],
  );

  const loadExample = useCallback((example: Example) => {
    setIncident(toForm(example.incident as unknown as Record<string, unknown>));
    setResult(null);
    setDetail(null);
    setBaseline(null);
    setError(null);
  }, []);

  const recentCases = useMemo(() => cases.filter((c) => c.latest), [cases]);

  return (
    <div className="min-h-screen bg-base-200 text-base-content">
      <div className="navbar min-h-14 gap-3 border-b border-base-300 bg-base-100 px-4 sm:px-6">
        <div className="navbar-start gap-3">
          <span className="text-sm font-medium tracking-tight">Incident desk</span>
          <span className="hidden text-sm text-base-content/50 xl:inline">
            Past cases and runbooks. Nothing here applies a fix.
          </span>
        </div>
        <div className="navbar-end gap-2">
          {recentCases.length > 0 && (
            <select
              className="select select-xs hidden w-56 md:block"
              value=""
              disabled={busy}
              onChange={(event) => event.target.value && openCase(event.target.value)}
              aria-label="Open a recent case"
            >
              <option value="">Open a recent case</option>
              {recentCases.map((c) => (
                <option key={c.case_id} value={c.case_id}>
                  {c.case_id} · {c.title}
                </option>
              ))}
            </select>
          )}
          <input
            className="input input-xs w-36"
            value={author}
            onChange={(event) => setAuthor(event.target.value)}
            placeholder="Your name"
            aria-label="Investigator"
          />
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
            regionPolicy={regionPolicy}
            openCaseId={result?.case_id ?? null}
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
                Describe the incident, or load one of the {examples.length} examples.
                The result is a cited investigation, or an escalation report when
                the evidence does not support a cause. Each run is saved on its
                case, so you can add what you learn and run it again.
              </p>
            </Panel>
          )}

          {result && (
            <>
              <CaseBar
                result={result}
                revisions={detail?.revisions ?? []}
                onOpenRevision={openRevision}
                busy={busy}
              />
              <Recommendation result={result} onCite={setSourceId} />
              <ConflictPanel
                conflicts={result.evidence.conflicts}
                onCite={setSourceId}
                onRecordFinding={viewingLatest ? recordFinding : undefined}
                busy={busy}
              />
              {viewingLatest && (
                <CaseEvidence
                  detail={detail}
                  formEdited={formEdited}
                  busy={busy}
                  onAttach={attach}
                  onRerun={rerunCase}
                />
              )}
              <ComparableCases
                candidates={result.evidence.candidates}
                excluded={result.evidence.excluded}
                regionPolicy={regionPolicy}
                onCite={setSourceId}
              />
              <RunbookPanel runbooks={result.evidence.runbooks} onCite={setSourceId} />
              <p className="px-1 text-xs text-base-content/45">
                Packet captures and file samples are not analysed in this version.
              </p>
            </>
          )}
        </div>

        {sourceId && <SourcePanel sourceId={sourceId} onClose={() => setSourceId(null)} />}
      </main>
    </div>
  );
}

/** The form's fields only; notes and findings travel as separate artifacts. */
function toForm(payload: Record<string, unknown>): CurrentIncident {
  const merged = { ...EMPTY, ...payload } as CurrentIncident;
  return {
    title: merged.title,
    description: merged.description,
    service: merged.service,
    environment: merged.environment,
    severity: merged.severity,
    region: merged.region,
    started_at: merged.started_at ? merged.started_at.slice(0, 16) : null,
    symptoms: merged.symptoms,
    suspected_dependencies: merged.suspected_dependencies,
    tech_context: merged.tech_context,
    events: merged.events.map((event) => ({ ...event, at: event.at.slice(0, 16) })),
    log_excerpt: merged.log_excerpt,
  };
}

function incidentKey(incident: CurrentIncident): string {
  return JSON.stringify(toForm(incident as unknown as Record<string, unknown>));
}
