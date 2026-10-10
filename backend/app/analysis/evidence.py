"""The evidence pack: everything the model is allowed to reason from.

Assembled by retrieval, judged by the gate, and the only context the prompt ever
sees. If an id is not in here, a citation naming it is invalid by definition.
"""

from dataclasses import dataclass, field

from app.analysis.conflicts import ConflictingFix
from app.analysis.temporal import TimelineAlignment, describe
from app.domain import (
    CurrentIncident,
    Incident,
    LogExcerpt,
    Postmortem,
    ResponseMode,
    Runbook,
)


@dataclass(frozen=True)
class CandidateCase:
    incident: Incident
    semantic: float
    metadata_score: float
    metadata_reasons: tuple[str, ...]
    timeline: TimelineAlignment
    recency: float
    service_graph: float
    graph_reason: str | None
    score: float
    age_days: int
    flags: tuple[str, ...]
    postmortem: Postmortem | None = None
    logs: tuple[LogExcerpt, ...] = ()

    @property
    def timeline_sequence(self) -> float:
        return self.timeline.sequence_score

    @property
    def timeline_interval(self) -> float:
        return self.timeline.interval_score

    @property
    def is_stale(self) -> bool:
        return "stale" in self.flags

    def citation_ids(self) -> tuple[str, ...]:
        ids = [self.incident.incident_id]
        if self.postmortem:
            ids.append(self.postmortem.postmortem_id)
        ids.extend(log.log_id for log in self.logs)
        return tuple(ids)

    def to_dict(self) -> dict[str, object]:
        incident = self.incident
        return {
            "incident_id": incident.incident_id,
            "title": incident.title,
            "summary": incident.summary,
            "service": incident.service,
            "environment": incident.environment.value,
            "severity": incident.severity.value,
            "region": incident.region,
            "started_at": incident.started_at.isoformat(timespec="minutes"),
            "duration_minutes": incident.duration_minutes,
            "age_days": self.age_days,
            "root_cause": incident.root_cause,
            "resolution": incident.resolution,
            "symptoms": list(incident.symptoms),
            "tech_context": list(incident.tech_context),
            "actions": [a.describe() for a in incident.actions],
            "score": round(self.score, 3),
            "components": {
                "semantic": round(self.semantic, 3),
                "metadata": round(self.metadata_score, 3),
                "timeline_sequence": round(self.timeline_sequence, 3),
                "timeline_interval": round(self.timeline_interval, 3),
                "recency": round(self.recency, 3),
                "service_graph": round(self.service_graph, 3),
            },
            "metadata_reasons": list(self.metadata_reasons),
            "graph_reason": self.graph_reason,
            "flags": list(self.flags),
            "timeline": self.timeline.to_dict(),
            "timeline_summary": describe(self.timeline),
            "postmortem_id": self.postmortem.postmortem_id if self.postmortem else None,
            "log_ids": [log.log_id for log in self.logs],
            "citation_ids": list(self.citation_ids()),
        }


@dataclass(frozen=True)
class RunbookMatch:
    runbook: Runbook
    semantic: float
    score: float
    reason: str

    def to_dict(self) -> dict[str, object]:
        runbook = self.runbook
        return {
            "runbook_id": runbook.runbook_id,
            "title": runbook.title,
            "trigger": runbook.trigger,
            "service": runbook.service,
            "steps": list(runbook.steps),
            "escalate_to": runbook.escalate_to,
            "version": runbook.version,
            "updated_at": runbook.updated_at.isoformat(timespec="minutes"),
            "score": round(self.score, 3),
            "semantic": round(self.semantic, 3),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ExcludedCase:
    """Filtered out, but reported: a silent filter is impossible to review."""

    incident_id: str
    title: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class EvidencePack:
    current: CurrentIncident
    candidates: tuple[CandidateCase, ...]
    runbooks: tuple[RunbookMatch, ...]
    conflicts: tuple[ConflictingFix, ...]
    excluded: tuple[ExcludedCase, ...]
    unknowns: tuple[str, ...]
    mode: ResponseMode
    confidence: float
    gate_reason: str
    escalate_to: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def citation_ids(self) -> frozenset[str]:
        """Every id a recommendation is allowed to cite."""
        ids: set[str] = set()
        for candidate in self.candidates:
            ids.update(candidate.citation_ids())
        ids.update(match.runbook.runbook_id for match in self.runbooks)
        return frozenset(ids)

    @property
    def top(self) -> CandidateCase | None:
        return self.candidates[0] if self.candidates else None

    def to_dict(self) -> dict[str, object]:
        current = self.current
        return {
            "current_incident": {
                "title": current.title,
                "description": current.description,
                "service": current.service,
                "environment": current.environment.value,
                "severity": current.severity.value,
                "region": current.region,
                "started_at": current.started_at.isoformat(timespec="minutes")
                if current.started_at
                else None,
                "symptoms": list(current.symptoms),
                "suspected_dependencies": list(current.suspected_dependencies),
                "tech_context": list(current.tech_context),
                "events": [
                    {
                        "at": event.at.isoformat(timespec="minutes"),
                        "type": event.type.value,
                        "service": event.service,
                        "description": event.description,
                    }
                    for event in current.events
                ],
                "log_excerpt": current.log_excerpt,
            },
            "mode": self.mode.value,
            "confidence": round(self.confidence, 3),
            "gate_reason": self.gate_reason,
            "escalate_to": self.escalate_to,
            "candidates": [c.to_dict() for c in self.candidates],
            "runbooks": [r.to_dict() for r in self.runbooks],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "excluded": [e.to_dict() for e in self.excluded],
            "unknowns": list(self.unknowns),
            "notes": list(self.notes),
            "citation_ids": sorted(self.citation_ids()),
        }
