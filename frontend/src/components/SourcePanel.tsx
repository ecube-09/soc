import { useEffect, useState } from "react";

import { fetchSource } from "../api/client";
import type { Source } from "../api/types";
import { Eyebrow } from "./bits";

type State =
  | { kind: "loading" }
  | { kind: "loaded"; source: Source }
  | { kind: "error"; message: string };

/** Resolves a clicked citation to the document it points at. */
export function SourcePanel({
  sourceId,
  onClose,
}: {
  sourceId: string;
  onClose: () => void;
}) {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: "loading" });
    fetchSource(sourceId, controller.signal)
      .then((source) => setState({ kind: "loaded", source }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          kind: "error",
          message: error instanceof Error ? error.message : "Could not load source",
        });
      });
    return () => controller.abort();
  }, [sourceId]);

  return (
    <aside className="card card-border sticky top-5 max-h-[calc(100vh-6.5rem)] overflow-y-auto bg-base-100">
      <div className="card-body gap-3 p-5">
        <div className="flex items-center justify-between gap-3">
          <Eyebrow>
            Source <span className="font-mono normal-case tracking-normal">{sourceId}</span>
          </Eyebrow>
          <button type="button" className="btn btn-xs btn-ghost" onClick={onClose}>
            Close
          </button>
        </div>

        {state.kind === "loading" && (
          <span className="loading loading-spinner loading-sm" />
        )}
        {state.kind === "error" && (
          <div role="alert" className="alert alert-error alert-soft text-sm">
            <span>{state.message}</span>
          </div>
        )}
        {state.kind === "loaded" && (
          <>
            <p className="text-sm leading-snug">{state.source.title}</p>
            <span className="badge badge-ghost badge-sm w-fit">
              {state.source.source_type.replace("_", " ")}
            </span>
            <dl className="grid gap-2">
              {Object.entries(state.source.metadata)
                .filter(([, value]) => value)
                .map(([key, value]) => (
                  <div key={key}>
                    <dt className="text-[11px] uppercase tracking-[0.14em] text-base-content/40">
                      {key.replaceAll("_", " ")}
                    </dt>
                    <dd className="text-sm">{value}</dd>
                  </div>
                ))}
            </dl>
            <pre className="overflow-x-auto whitespace-pre-wrap rounded-box border border-base-300 bg-base-200/70 p-3 font-mono text-xs leading-relaxed">
              {state.source.body}
            </pre>
          </>
        )}
      </div>
    </aside>
  );
}
