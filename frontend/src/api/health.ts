export const API_PREFIX = "/api/v1";

export type ModelRole = "chat" | "embedding";

export interface ModelCheck {
  name: string;
  role: ModelRole;
  present: boolean;
}

export interface RuntimeCheck {
  provider: string;
  base_url: string;
  reachable: boolean;
  detail: string | null;
  models: ModelCheck[];
  available_models: string[];
}

export interface HealthResponse {
  status: "ok" | "degraded";
  app_name: string;
  version: string;
  runtime: RuntimeCheck;
}

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch(`${API_PREFIX}/health`, { signal });
  if (!response.ok) {
    throw new Error(`Health check failed with HTTP ${response.status}`);
  }
  return (await response.json()) as HealthResponse;
}
