"""Cases: the unit of work, and the loop an engineer actually runs.

Read the plan, go and measure something, come back with a number. Each return
trip adds an artifact to the case - a note, a log excerpt, or a finding that
settles a conflict - and re-runs the same pipeline. Every run is stored as a
revision, so the case shows how the answer moved and why.

Two clocks are kept apart on purpose. `created_at` is the wall clock: when a
person opened the case or re-ran it. `reference_time` is the incident's own
time, which is what recency and staleness are measured against, so reopening a
case next month does not silently rerank it.
"""

import uuid
from dataclasses import replace
from datetime import datetime
from typing import Any

from app.corpus import parse_current_incident, parse_finding
from app.domain import ArtifactKind, CurrentIncident, assert_artifact_kind_supported
from app.db.repository import SqliteRepository
from app.services.investigation import InvestigationService

UNATTRIBUTED = "unattributed"


class CaseNotFound(LookupError):
    pass


def new_case_id() -> str:
    return f"CASE-{uuid.uuid4().hex[:8].upper()}"


def new_artifact_id() -> str:
    return f"ART-{uuid.uuid4().hex[:8].upper()}"


def validate_payload(kind: ArtifactKind, payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise what each kind must carry. Raises ValueError with a readable reason."""
    assert_artifact_kind_supported(kind)

    if kind is ArtifactKind.NOTE:
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ValueError("A note needs some text.")
        return {"text": text}

    if kind is ArtifactKind.LOG_EXCERPT:
        text = payload.get("text")
        if not text and isinstance(payload.get("lines"), list):
            text = "\n".join(str(line) for line in payload["lines"])
        text = str(text or "").strip()
        if not text:
            raise ValueError("A log excerpt needs `text` or `lines`.")
        return {"text": text}

    if kind is ArtifactKind.FINDING:
        try:
            finding = parse_finding(payload)
        except KeyError as exc:
            raise ValueError(f"A finding needs `{exc.args[0]}`.") from exc
        return {
            "target": finding.target,
            "component": finding.component,
            "favours_incident_id": finding.favours_incident_id,
            "observation": finding.observation,
        }

    if kind is ArtifactKind.INCIDENT:
        try:
            parse_current_incident(payload)
        except KeyError as exc:
            raise ValueError(f"The incident is missing `{exc.args[0]}`.") from exc
        return payload

    return payload


class CaseService:
    def __init__(self, repo: SqliteRepository, investigations: InvestigationService) -> None:
        self.repo = repo
        self.investigations = investigations

    def add_artifact(
        self,
        case_id: str,
        kind: ArtifactKind,
        payload: dict[str, Any],
        *,
        author: str | None,
    ) -> dict[str, Any]:
        normalised = validate_payload(kind, payload)
        now = datetime.now()
        artifact_id = new_artifact_id()
        if kind is ArtifactKind.INCIDENT:
            current = parse_current_incident(normalised)
            self.repo.create_case(
                case_id,
                current.title,
                now,
                created_by=author,
                incident_started_at=current.anchor_time,
            )
        elif self.repo.case(case_id) is None:
            self.repo.create_case(case_id, f"Case {case_id}", now, created_by=author)
        self.repo.add_artifact(
            artifact_id=artifact_id,
            case_id=case_id,
            kind=kind.value,
            payload=normalised,
            created_at=now,
            author=author,
        )
        return {
            "artifact_id": artifact_id,
            "case_id": case_id,
            "kind": kind.value,
            "created_at": now.isoformat(timespec="seconds"),
            "author": author,
        }

    def incident_for(self, case_id: str) -> CurrentIncident:
        """The latest incident artifact, with every later note, log, and finding."""
        artifacts = self.repo.case_artifacts(case_id)
        incidents = [a for a in artifacts if a["kind"] == ArtifactKind.INCIDENT.value]
        if not incidents:
            raise CaseNotFound(f"Case {case_id} has no incident to investigate.")
        base = parse_current_incident(incidents[-1]["payload"])

        notes = [a["payload"]["text"] for a in artifacts if a["kind"] == ArtifactKind.NOTE.value]
        logs = [
            a["payload"]["text"]
            for a in artifacts
            if a["kind"] == ArtifactKind.LOG_EXCERPT.value
        ]
        findings = [
            parse_finding({**a["payload"], "author": a.get("author")})
            for a in artifacts
            if a["kind"] == ArtifactKind.FINDING.value
        ]
        log_excerpt = "\n".join(text for text in (base.log_excerpt, *logs) if text) or None
        return replace(
            base,
            notes=(*base.notes, *notes),
            findings=(*base.findings, *findings),
            log_excerpt=log_excerpt,
        )

    async def start(
        self,
        incident: dict[str, Any],
        *,
        author: str | None,
        case_id: str | None = None,
    ) -> dict[str, Any]:
        case_id = case_id or new_case_id()
        self.add_artifact(case_id, ArtifactKind.INCIDENT, incident, author=author)
        return await self.run(case_id, author=author)

    async def run(
        self,
        case_id: str,
        *,
        author: str | None,
        incident: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Investigate the case as it stands now and store the result as a revision."""
        if self.repo.case(case_id) is None:
            raise CaseNotFound(f"No case {case_id}.")
        if incident is not None:
            self.add_artifact(case_id, ArtifactKind.INCIDENT, incident, author=author)

        current = self.incident_for(case_id)
        previous = self.repo.investigation(case_id)
        result = await self.investigations.investigate(current)

        now = datetime.now()
        revision = (previous["revision"] if previous else 0) + 1
        counts = {
            "notes": len(current.notes),
            "findings": len(current.findings),
            "log_excerpts": sum(
                1
                for a in self.repo.case_artifacts(case_id)
                if a["kind"] == ArtifactKind.LOG_EXCERPT.value
            ),
        }
        body: dict[str, Any] = {
            **result.to_dict(),
            "case_id": case_id,
            "revision": revision,
            "author": author,
            "created_at": now.isoformat(timespec="seconds"),
            "reference_time": current.anchor_time.isoformat(timespec="minutes")
            if current.anchor_time
            else None,
            "evidence_counts": counts,
        }
        body["previous"] = _summary(previous) if previous else None
        body["changes"] = describe_changes(previous, body)
        body["rendered"] = result.render_text()
        body["rendered_markdown"] = result.render_markdown(
            _report_header(case_id, revision, author, now, current)
        )

        self.repo.save_investigation(
            case_id=case_id,
            result=body,
            created_at=now,
            reference_time=current.anchor_time,
            author=author,
        )
        return body

    def detail(self, case_id: str) -> dict[str, Any]:
        case = self.repo.case(case_id)
        if case is None:
            raise CaseNotFound(f"No case {case_id}.")
        return {
            **case,
            "artifacts": [
                {**a, "case_id": case_id} for a in self.repo.case_artifacts(case_id)
            ],
            "revisions": self.repo.investigation_summaries(case_id),
        }


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    candidates = result.get("evidence", {}).get("candidates") or []
    return {
        "revision": result.get("revision"),
        "mode": result["mode"],
        "confidence": result["confidence"],
        "root_cause_status": result["root_cause_status"],
        "top_incident_id": candidates[0]["incident_id"] if candidates else None,
        "created_at": result.get("created_at"),
        "author": result.get("author"),
    }


def _resolutions(result: dict[str, Any]) -> dict[str, str]:
    return {
        c["target"]: c["resolution"]["favours_incident_id"]
        for c in result.get("evidence", {}).get("conflicts", [])
        if c.get("resolution")
    }


def describe_changes(previous: dict[str, Any] | None, current: dict[str, Any]) -> list[str]:
    """Plain sentences on how this revision differs from the last one."""
    if previous is None:
        return []

    changes: list[str] = []
    before = previous.get("evidence_counts") or {}
    after = current.get("evidence_counts") or {}
    added = [
        f"{after.get(key, 0) - before.get(key, 0)} {label}"
        for key, label in (
            ("notes", "note(s)"),
            ("log_excerpts", "log excerpt(s)"),
            ("findings", "finding(s)"),
        )
        if after.get(key, 0) > before.get(key, 0)
    ]
    if added:
        changes.append(
            f"Since revision {previous.get('revision')}: {', '.join(added)} added."
        )

    if previous["mode"] != current["mode"]:
        gate_mode = current.get("evidence", {}).get("mode", current["mode"])
        why = (
            " The evidence still allowed an investigation; none of the drafted steps "
            "survived the checks."
            if gate_mode != current["mode"]
            else ""
        )
        changes.append(f"Moved from {previous['mode']} to {current['mode']}.{why}")
    if abs(previous["confidence"] - current["confidence"]) >= 0.005:
        changes.append(
            f"Confidence went from {previous['confidence']:.0%} to {current['confidence']:.0%}."
        )

    old_top = _summary(previous)["top_incident_id"]
    new_top = _summary(current)["top_incident_id"]
    if old_top != new_top:
        changes.append(f"Closest case changed from {old_top or 'none'} to {new_top or 'none'}.")

    old_res, new_res = _resolutions(previous), _resolutions(current)
    for target, favours in new_res.items():
        if old_res.get(target) != favours:
            changes.append(f"Conflict on {target} settled in favour of {favours}.")

    if len(changes) == (1 if added else 0):
        changes.append("No change in mode, confidence, or closest case.")
    return changes


def _report_header(
    case_id: str,
    revision: int,
    author: str | None,
    now: datetime,
    current: CurrentIncident,
) -> list[str]:
    lines = [
        f"- **Case:** {case_id}, revision {revision}",
        f"- **Investigator:** {author or UNATTRIBUTED}",
    ]
    if current.anchor_time:
        lines.append(f"- **Incident time:** {current.anchor_time:%Y-%m-%d %H:%M}")
    lines.append(f"- **Report generated:** {now:%Y-%m-%d %H:%M}")
    for note in current.notes:
        lines.append(f"- **Engineer note:** {note}")
    for finding in current.findings:
        lines.append(
            f"- **Recorded measurement:** {finding.observation} "
            f"(favours {finding.favours_incident_id} on {finding.target})"
        )
    return lines
