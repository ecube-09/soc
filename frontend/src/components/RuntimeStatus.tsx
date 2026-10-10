import { useCallback, useEffect, useState } from "react";

import { fetchStats } from "../api/client";
import { fetchHealth, type HealthResponse } from "../api/health";
import type { CorpusStats } from "../api/types";

type State =
  | { kind: "loading" }
  | { kind: "loaded"; health: HealthResponse; stats: CorpusStats | null }
  | { kind: "error"; message: string };

export function RuntimeStatus() {
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback((signal?: AbortSignal) => {
    setState({ kind: "loading" });
    Promise.all([fetchHealth(signal), fetchStats(signal).catch(() => null)])
      .then(([health, stats]) => setState({ kind: "loaded", health, stats }))
      .catch((error: unknown) => {
        if (signal?.aborted) return;
        setState({
          kind: "error",
          message:
            error instanceof Error ? error.message : "Could not reach the backend",
        });
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const ok = state.kind === "loaded" && state.health.status === "ok";
  const label =
    state.kind === "loading"
      ? "Checking"
      : state.kind === "error"
        ? "Offline"
        : state.health.status;

  return (
    <details className="dropdown dropdown-end">
      <summary className="btn btn-sm btn-ghost gap-2 font-normal">
        <span
          className={`status status-sm ${ok ? "status-success" : "status-warning"}`}
        />
        {label}
      </summary>
      <div className="dropdown-content z-20 mt-2 w-80 rounded-box border border-base-300 bg-base-100 p-4">
        {state.kind === "error" && (
          <div role="alert" className="alert alert-error alert-soft text-sm">
            <span>
              {state.message}. Start the backend with{" "}
              <code className="font-mono text-xs">uvicorn app.main:app --reload</code>.
            </span>
          </div>
        )}
        {state.kind === "loaded" && (
          <div className="grid gap-3 text-sm">
            <p className="text-base-content/60">
              {state.health.runtime.provider}{" "}
              <span className="font-mono text-xs">
                {state.health.runtime.base_url}
              </span>
            </p>
            <ul className="list">
              {state.health.runtime.models.map((model) => (
                <li className="list-row px-0" key={model.role}>
                  <span
                    className={`badge badge-sm ${model.present ? "badge-ghost" : "badge-soft badge-warning"}`}
                  >
                    {model.present ? "present" : "missing"}
                  </span>
                  <span className="font-mono text-xs">{model.name}</span>
                  <span className="text-xs text-base-content/45">{model.role}</span>
                </li>
              ))}
            </ul>
            {!state.health.runtime.reachable && (
              <p className="text-sm text-error">
                Ollama is not reachable. Start it with{" "}
                <code className="font-mono text-xs">ollama serve</code>.
              </p>
            )}
            {state.stats && (
              <p className="text-xs leading-relaxed text-base-content/55">
                {state.stats.counts.incidents} incidents,{" "}
                {state.stats.counts.runbooks} runbooks,{" "}
                {state.stats.counts.postmortems} postmortems,{" "}
                {state.stats.indexed_chunks} indexed chunks.
                {state.stats.indexed_chunks === 0 && (
                  <>
                    {" "}
                    Nothing is indexed. Run{" "}
                    <code className="font-mono">python -m app.cli seed</code> then{" "}
                    <code className="font-mono">python -m app.cli ingest</code>.
                  </>
                )}
              </p>
            )}
          </div>
        )}
        <button
          type="button"
          className="btn btn-sm btn-ghost mt-3"
          onClick={() => load()}
          disabled={state.kind === "loading"}
        >
          {state.kind === "loading" ? (
            <span className="loading loading-spinner loading-xs" />
          ) : (
            "Re-check"
          )}
        </button>
      </div>
    </details>
  );
}
