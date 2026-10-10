"""Result types and rendering.

The escalation report is assembled from the evidence pack rather than written by
the model, so the fields are present and correct even when generation is thin or
fails outright. Its purpose is to hand a human something useful about an
incident the system could not explain.
"""

from dataclasses import dataclass, field

from app.analysis.evidence import EvidencePack
from app.domain import ResponseMode, RootCauseStatus


@dataclass(frozen=True)
class InvestigationStep:
    action: str
    rationale: str
    citations: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "rationale": self.rationale,
            "citations": list(self.citations),
        }


@dataclass(frozen=True)
class EscalationReport:
    incident_title: str
    severity: str
    service: str
    environment: str
    observed_symptoms: tuple[str, ...]
    timeline: tuple[str, ...]
    historical_correlation: tuple[str, ...]
    evidence_gathered: tuple[str, ...]
    investigations_considered: tuple[InvestigationStep, ...]
    unresolved: tuple[str, ...]
    requested_team: str
    recommended_next_investigation: tuple[str, ...]
    citations: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "incident_title": self.incident_title,
            "severity": self.severity,
            "service": self.service,
            "environment": self.environment,
            "observed_symptoms": list(self.observed_symptoms),
            "timeline": list(self.timeline),
            "historical_correlation": list(self.historical_correlation),
            "evidence_gathered": list(self.evidence_gathered),
            "investigations_considered": [
                s.to_dict() for s in self.investigations_considered
            ],
            "unresolved": list(self.unresolved),
            "requested_team": self.requested_team,
            "recommended_next_investigation": list(self.recommended_next_investigation),
            "citations": list(self.citations),
        }

    def render_text(self) -> str:
        lines = [
            "INCIDENT ESCALATION REPORT",
            "",
            f"Incident: {self.incident_title}",
            f"Severity: {self.severity}",
            f"Affected service: {self.service} ({self.environment})",
            "",
            "Observed symptoms:",
            *_bullets(self.observed_symptoms),
            "",
            "Timeline:",
            *_bullets(self.timeline, marker="  "),
            "",
            "Historical correlation:",
            *_bullets(self.historical_correlation),
            "",
            "Evidence gathered:",
            *_bullets(self.evidence_gathered),
        ]

        if self.investigations_considered:
            lines += ["", "Investigations considered:"]
            for step in self.investigations_considered:
                lines.append(f"  - {step.action}")
                lines.append(f"    {step.rationale}")
                lines.append(f"    Sources: {', '.join(step.citations)}")

        lines += [
            "",
            "Unresolved:",
            *_bullets(self.unresolved),
            "",
            f"Requested team: {self.requested_team}",
            "",
            "Recommended next investigation:",
            *_bullets(self.recommended_next_investigation),
            "",
            "No remediation has been applied. This report states what is known "
            "and what is not.",
        ]
        if self.citations:
            lines += ["", f"Sources: {', '.join(self.citations)}"]
        return "\n".join(lines)


@dataclass(frozen=True)
class InvestigationResult:
    mode: ResponseMode
    root_cause_status: RootCauseStatus
    confidence: float
    gate_reason: str
    headline: str
    root_cause_hypothesis: str | None
    steps: tuple[InvestigationStep, ...]
    cautions: tuple[str, ...]
    unresolved: tuple[str, ...]
    evidence: EvidencePack
    escalation: EscalationReport | None = None
    validation_notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def citations(self) -> tuple[str, ...]:
        seen: list[str] = []
        for step in self.steps:
            for citation in step.citations:
                if citation not in seen:
                    seen.append(citation)
        return tuple(seen)

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "root_cause_status": self.root_cause_status.value,
            "confidence": round(self.confidence, 3),
            "gate_reason": self.gate_reason,
            "headline": self.headline,
            "root_cause_hypothesis": self.root_cause_hypothesis,
            "steps": [s.to_dict() for s in self.steps],
            "cautions": list(self.cautions),
            "unresolved": list(self.unresolved),
            "citations": list(self.citations),
            "validation_notes": list(self.validation_notes),
            "escalation_report": self.escalation.to_dict() if self.escalation else None,
            "evidence": self.evidence.to_dict(),
        }

    def render_text(self) -> str:
        if self.escalation:
            return self.escalation.render_text()

        lines = [self.headline, ""]
        if self.root_cause_hypothesis:
            lines += [
                f"Root cause ({self.root_cause_status.value}): "
                f"{self.root_cause_hypothesis}",
                "",
            ]
        lines.append("Recommended investigation:")
        for index, step in enumerate(self.steps, start=1):
            lines.append(f"  {index}. {step.action}")
            lines.append(f"     {step.rationale}")
            lines.append(f"     Sources: {', '.join(step.citations)}")
        if self.cautions:
            lines += ["", "Cautions:", *_bullets(self.cautions)]
        if self.unresolved:
            lines += ["", "Unresolved:", *_bullets(self.unresolved)]
        lines += [
            "",
            "Nothing here has been applied. An engineer decides and acts.",
        ]
        if self.validation_notes:
            lines += ["", "Validation:", *_bullets(self.validation_notes)]
        return "\n".join(lines)


def _bullets(values, marker: str = "  - ") -> list[str]:
    return [f"{marker}{value}" for value in values] or [f"{marker}none recorded"]
