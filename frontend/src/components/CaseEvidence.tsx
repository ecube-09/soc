import { useState } from "react";

import type { Artifact, CaseDetail } from "../api/types";
import { Eyebrow, Panel } from "./bits";

type Tab = "note" | "log_excerpt";

interface Props {
  detail: CaseDetail | null;
  formEdited: boolean;
  busy: boolean;
  onAttach: (kind: Tab, payload: Record<string, unknown>) => Promise<void>;
  onRerun: () => void;
}

/**
 * The second pass: what the engineer learned after the first answer. Everything
 * here is attached to the case and only counts once the case is re-run.
 */
export function CaseEvidence({ detail, formEdited, busy, onAttach, onRerun }: Props) {
  const [tab, setTab] = useState<Tab>("note");
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);

  const added = (detail?.artifacts ?? []).filter((a) => a.kind !== "incident");
  const lastRun = (detail?.revisions ?? []).reduce<string>(
    (latest, r) => (r.created_at && r.created_at > latest ? r.created_at : latest),
    "",
  );
  const pending = added.filter((a) => a.created_at > lastRun).length;

  const attach = async () => {
    if (!text.trim()) return;
    setSaving(true);
    try {
      await onAttach(tab, { text: text.trim() });
      setText("");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Panel>
      <div className="flex items-center justify-between gap-3">
        <Eyebrow>Add to this case</Eyebrow>
        <div role="tablist" className="tabs tabs-box tabs-xs">
          <button
            type="button"
            role="tab"
            className={`tab ${tab === "note" ? "tab-active" : ""}`}
            onClick={() => setTab("note")}
          >
            Note
          </button>
          <button
            type="button"
            role="tab"
            className={`tab ${tab === "log_excerpt" ? "tab-active" : ""}`}
            onClick={() => setTab("log_excerpt")}
          >
            Log lines
          </button>
        </div>
      </div>

      <textarea
        className={`textarea textarea-sm w-full leading-relaxed ${
          tab === "log_excerpt" ? "font-mono text-xs" : ""
        }`}
        rows={3}
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder={
          tab === "note"
            ? "What you checked and what you saw. Measurements that settle a conflict go in the conflict panel."
            : "Paste the lines you pulled since the first pass."
        }
      />

      <div className="flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          className="btn btn-sm"
          disabled={saving || busy || !text.trim()}
          onClick={attach}
        >
          {saving && <span className="loading loading-spinner loading-xs" />}
          Attach {tab === "note" ? "note" : "log lines"}
        </button>
        <button
          type="button"
          className="btn btn-sm btn-neutral"
          disabled={busy}
          onClick={onRerun}
        >
          {busy && <span className="loading loading-spinner loading-xs" />}
          Re-run{rerunHint(pending, formEdited)}
        </button>
      </div>

      {added.length > 0 && (
        <ul className="list border-t border-base-300">
          {added.map((artifact) => (
            <ArtifactRow
              key={artifact.artifact_id}
              artifact={artifact}
              pending={artifact.created_at > lastRun}
            />
          ))}
        </ul>
      )}
    </Panel>
  );
}

function ArtifactRow({ artifact, pending }: { artifact: Artifact; pending: boolean }) {
  const payload = artifact.payload;
  const body =
    artifact.kind === "finding"
      ? `${String(payload.observation ?? "")} → favours ${String(payload.favours_incident_id ?? "")}`
      : String(payload.text ?? (payload.lines as string[] | undefined)?.join("\n") ?? "");

  return (
    <li className="list-row px-0 py-2">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-base-content/45">
          <span className="badge badge-xs badge-ghost">{artifact.kind.replace("_", " ")}</span>
          <span>{artifact.author ?? "unattributed"}</span>
          <span>{artifact.created_at.slice(0, 16).replace("T", " ")}</span>
          {pending && <span className="badge badge-xs badge-soft badge-info">not run yet</span>}
        </div>
        <p
          className={`mt-1 whitespace-pre-wrap text-sm leading-relaxed text-base-content/80 ${
            artifact.kind === "log_excerpt" ? "font-mono text-xs" : ""
          }`}
        >
          {body}
        </p>
      </div>
    </li>
  );
}

function rerunHint(pending: number, formEdited: boolean): string {
  const parts = [];
  if (pending) parts.push(`${pending} new`);
  if (formEdited) parts.push("edited incident");
  return parts.length ? ` with ${parts.join(" + ")}` : "";
}
