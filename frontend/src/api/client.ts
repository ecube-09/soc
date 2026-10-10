import { API_PREFIX } from "./health";
import type {
  CorpusStats,
  CurrentIncident,
  Example,
  InvestigationResponse,
  Service,
  Source,
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
  signal?: AbortSignal,
): Promise<InvestigationResponse> {
  return request<InvestigationResponse>("/investigations", {
    method: "POST",
    body: JSON.stringify({ incident }),
    signal,
  });
}
