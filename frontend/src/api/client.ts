import { API_PREFIX } from "./health";
import type {
  Artifact,
  CaseDetail,
  CaseSummary,
  CorpusStats,
  CurrentIncident,
  EvidenceKind,
  Example,
  InvestigationResponse,
  Service,
  Source,
  TimelineExtraction,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });

  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as T;
}

/** Surface the backend's own explanation; it is written for the operator. */
async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") return body.detail;
    if (Array.isArray(body?.detail)) {
      return body.detail
        .map((item: { loc?: string[]; msg?: string }) =>
          [item.loc?.slice(1).join("."), item.msg].filter(Boolean).join(": "),
        )
        .join("; ");
    }
  } catch {
    // fall through to the status line
  }
  return `Request failed with HTTP ${response.status}`;
}

export function fetchExamples(signal?: AbortSignal): Promise<Example[]> {
  return request<Example[]>("/examples", { signal });
}

export function fetchServices(signal?: AbortSignal): Promise<Service[]> {
  return request<Service[]>("/services", { signal });
}

export function fetchStats(signal?: AbortSignal): Promise<CorpusStats> {
  return request<CorpusStats>("/stats", { signal });
}

export function fetchSource(
  sourceId: string,
  signal?: AbortSignal,
): Promise<Source> {
  return request<Source>(`/sources/${encodeURIComponent(sourceId)}`, { signal });
}

export function investigate(
  incident: CurrentIncident,
  author: string | null,
  signal?: AbortSignal,
): Promise<InvestigationResponse> {
  return request<InvestigationResponse>("/investigations", {
    method: "POST",
    body: JSON.stringify({ incident, author }),
    signal,
  });
}

export function fetchCases(signal?: AbortSignal): Promise<CaseSummary[]> {
  return request<CaseSummary[]>("/cases", { signal });
}

export function fetchCase(caseId: string, signal?: AbortSignal): Promise<CaseDetail> {
  return request<CaseDetail>(`/cases/${encodeURIComponent(caseId)}`, { signal });
}

export function fetchRevision(
  caseId: string,
  revision: number,
  signal?: AbortSignal,
): Promise<InvestigationResponse> {
  return request<InvestigationResponse>(
    `/cases/${encodeURIComponent(caseId)}/investigations/${revision}`,
    { signal },
  );
}

export function addEvidence(
  caseId: string,
  kind: EvidenceKind,
  payload: Record<string, unknown>,
  author: string | null,
): Promise<Omit<Artifact, "payload">> {
  return request(`/cases/${encodeURIComponent(caseId)}/artifacts`, {
    method: "POST",
    body: JSON.stringify({ kind, payload, author }),
  });
}

/** `incident` only when the form was edited; otherwise the case's own is re-used. */
export function rerun(
  caseId: string,
  author: string | null,
  incident?: CurrentIncident,
): Promise<InvestigationResponse> {
  return request<InvestigationResponse>(
    `/cases/${encodeURIComponent(caseId)}/investigations`,
    { method: "POST", body: JSON.stringify({ author, incident }) },
  );
}

export function reportUrl(
  caseId: string,
  revision: number,
  format: "markdown" | "text" = "markdown",
): string {
  return `${API_PREFIX}/cases/${encodeURIComponent(caseId)}/report?format=${format}&revision=${revision}`;
}

export function extractTimeline(
  logText: string,
  service: string,
  date: string | null,
): Promise<TimelineExtraction> {
  return request<TimelineExtraction>("/timeline/extract", {
    method: "POST",
    body: JSON.stringify({ log_text: logText, service, date }),
  });
}
