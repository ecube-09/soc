"""Result types and rendering.

The escalation report is assembled from the evidence pack rather than written by
the model, so the fields are present and correct even when generation is thin or
fails outright. Its purpose is to hand a human something useful about an
incident the system could not explain.

Both results render as plain text (the CLI) and as Markdown (the export an
on-call would actually paste into a ticket or chat).
"""

from dataclasses import dataclass, field

from app.analysis.evidence import EvidencePack
from app.domain import ResponseMode, RootCauseStatus

NOT_APPLIED_ESCALATION = (
    "No remediation has been applied. This report states what is known and what is not."
)
NOT_APPLIED_INVESTIGATION = "Nothing here has been applied. An engineer decides and acts."


@dataclass(frozen=True)
class InvestigationStep:
    action: str
    rationale: str
    citations: tuple[str, ...]
    would_falsify: str = ""
    # Best lexical coverage of the step by one of its cited documents.
    support: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "rationale": self.rationale,
            "citations": list(self.citations),
            "would_falsify": self.would_falsify,
            "support": self.support,
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
    conflicting_guidance: tuple[str, ...] = ()

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
            "conflicting_guidance": list(self.conflicting_guidance),
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
        if self.conflicting_guidance:
            lines += ["", "Conflicting historical guidance:", *_bullets(self.conflicting_guidance)]

        if self.investigations_considered:
            lines += ["", "Investigations considered:"]
            for step in self.investigations_considered:
                lines.append(f"  - {step.action}")
                if step.rationale:
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
            NOT_APPLIED_ESCALATION,
        ]
        if self.citations:
            lines += ["", f"Sources: {', '.join(self.citations)}"]
        return "\n".join(lines)

    def render_markdown(self, header: list[str] | None = None) -> str:
        lines = [
            f"# Escalation: {self.incident_title}",
            "",
            *(header or []),
            f"- **Severity:** {self.severity}",
            f"- **Affected service:** {self.service} ({self.environment})",
            f"- **Requested team:** {self.requested_team}",
            "- **Root cause:** undetermined from the available evidence",
            "",
            "## Observed symptoms",
            *_md_bullets(self.observed_symptoms),
            "",
            "## Timeline",
            "```",
            *(self.timeline or ("none recorded",)),
            "```",
            "",
            "## Historical correlation",
            *_md_bullets(self.historical_correlation),
            "",
            "## Evidence gathered",
            *_md_bullets(self.evidence_gathered),
        ]
        if self.conflicting_guidance:
            lines += ["", "## Conflicting historical guidance", *_md_bullets(self.conflicting_guidance)]
        if self.investigations_considered:
            lines += ["", "## Investigations considered", *_md_steps(self.investigations_considered)]
        lines += [
            "",
            "## Unresolved",
            *_md_bullets(self.unresolved),
            "",
            "## Recommended next investigation",
            *_md_numbered(self.recommended_next_investigation),
            "",
            f"_{NOT_APPLIED_ESCALATION}_",
        ]
        if self.citations:
            lines += ["", f"Sources: {', '.join(f'`{c}`' for c in self.citations)}"]
        return "\n".join(lines) + "\n"


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
    hypothesis_basis: tuple[str, ...] = ()

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
            "hypothesis_basis": list(self.hypothesis_basis),
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
            if step.rationale:
                lines.append(f"     {step.rationale}")
            if step.would_falsify:
                lines.append(f"     Would count against it: {step.would_falsify}")
            lines.append(f"     Sources: {', '.join(step.citations)}")
        conflicts = conflict_lines(self.evidence)
        if conflicts:
            lines += ["", "Conflicting historical guidance:", *_bullets(conflicts)]
        if self.cautions:
            lines += ["", "Cautions:", *_bullets(self.cautions)]
        if self.unresolved:
            lines += ["", "Unresolved:", *_bullets(self.unresolved)]
        lines += ["", NOT_APPLIED_INVESTIGATION]
        if self.validation_notes:
            lines += ["", "Validation:", *_bullets(self.validation_notes)]
        return "\n".join(lines)

    def render_markdown(self, header: list[str] | None = None) -> str:
        """The copyable artifact: what an on-call would paste into a ticket."""
        confidence_line = (
            f"- **Confidence:** {self.confidence:.0%} ({self.gate_reason})"
        )
        if self.escalation:
            return self.escalation.render_markdown([*(header or []), confidence_line])

        current = self.evidence.current
        lines = [
            f"# Investigation: {current.title}",
            "",
            *(header or []),
            f"- **Service:** {current.service} ({current.environment.value}), {current.severity.value}",
            confidence_line,
            "",
            self.headline,
            "",
        ]
        if self.root_cause_hypothesis:
            basis = (
                f" Based on {', '.join(f'`{b}`' for b in self.hypothesis_basis)}."
                if self.hypothesis_basis
                else ""
            )
            lines += [
                f"**Hypothesis (unconfirmed):** {self.root_cause_hypothesis}{basis}",
                "",
            ]
        lines += ["## Recommended checks", *_md_steps(self.steps)]
        conflicts = conflict_lines(self.evidence)
        if conflicts:
            lines += ["", "## Conflicting historical guidance", *_md_bullets(conflicts)]
        if self.cautions:
            lines += ["", "## Cautions", *_md_bullets(self.cautions)]
        if self.unresolved:
            lines += ["", "## Unresolved", *_md_bullets(self.unresolved)]
        lines += [
            "",
            "## Comparable cases",
            *_md_bullets(
                f"`{c.incident.incident_id}` {c.incident.title} — match {c.score:.2f}"
                f"{' (stale)' if c.is_stale else ''}"
                for c in self.evidence.candidates
            ),
            "",
            f"_{NOT_APPLIED_INVESTIGATION}_",
        ]
        if self.citations:
            lines += ["", f"Sources: {', '.join(f'`{c}`' for c in self.citations)}"]
        return "\n".join(lines) + "\n"


def conflict_lines(pack: EvidencePack) -> tuple[str, ...]:
    lines: list[str] = []
    for conflict in pack.conflicts:
        resolution = pack.resolution_for(conflict)
        if resolution:
            lines.append(
                f"{conflict.summary} Settled by measurement in favour of "
                f"{resolution.favours_incident_id}: {resolution.observation}"
            )
        else:
            lines.append(f"{conflict.summary} Deciding check: {conflict.distinguishing_question}")
    return tuple(lines)


def _bullets(values, marker: str = "  - ") -> list[str]:
    return [f"{marker}{value}" for value in values] or [f"{marker}none recorded"]


def _md_bullets(values) -> list[str]:
    return [f"- {value}" for value in values] or ["- none recorded"]


def _md_numbered(values) -> list[str]:
    return [f"{n}. {value}" for n, value in enumerate(values, start=1)] or ["- none recorded"]


def _md_steps(steps) -> list[str]:
    lines: list[str] = []
    for n, step in enumerate(steps, start=1):
        lines.append(f"{n}. {step.action}")
        if step.rationale:
            lines.append(f"   - Why: {step.rationale}")
        if step.would_falsify:
            lines.append(f"   - Would count against the hypothesis: {step.would_falsify}")
        lines.append(f"   - Sources: {', '.join(f'`{c}`' for c in step.citations)}")
    return lines or ["- none"]
