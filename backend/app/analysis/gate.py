"""The confidence gate.

This is the rule the project turns on: when the evidence does not support naming
a cause, the system escalates instead of guessing. It runs twice - once on the
retrieved evidence, before a single token is generated, and again on the model's
output to check that what it claimed is actually cited.

It is code rather than prompt text because a prompt is a request and this is a
requirement.
"""

from dataclasses import dataclass

from app.analysis.conflicts import ConflictingFix
from app.analysis.evidence import CandidateCase, RunbookMatch
from app.config import Settings
from app.domain import ResponseMode


@dataclass(frozen=True)
class GateDecision:
    mode: ResponseMode
    confidence: float
    reason: str
    unknowns: tuple[str, ...]
    escalate_to: str | None


def evaluate(
    *,
    has_timeline: bool,
    candidates: list[CandidateCase],
    runbooks: list[RunbookMatch],
    conflicts: list[ConflictingFix],
    settings: Settings,
) -> GateDecision:
    if not candidates:
        return GateDecision(
            mode=ResponseMode.ESCALATION,
            confidence=0.0,
            reason="No comparable historical incident survived retrieval and filtering.",
            unknowns=(
                "No historical case matches this service and environment, so the "
                "corpus cannot suggest a cause.",
            ),
            escalate_to=_escalation_target(runbooks),
        )

    top = candidates[0]
    confidence = top.score
    unknowns: list[str] = []
    penalties: list[str] = []

    if top.is_stale:
        confidence *= 0.6
        penalties.append("closest case predates the current stack")
        unknowns.append(
            f"{top.incident.incident_id} is {top.age_days} days old; its remediation "
            f"targets {', '.join(top.incident.tech_context) or 'an older stack'} and may "
            "not apply as written."
        )

    if not has_timeline:
        confidence *= 0.8
        penalties.append("no timeline supplied")
        unknowns.append(
            "No event timeline was provided, so historical cases could not be "
            "compared on how they unfolded."
        )
    elif not top.timeline.comparable:
        confidence *= 0.9
        penalties.append("closest case has no comparable timeline")

    relevant_conflicts = [
        conflict
        for conflict in conflicts
        if set(conflict.incident_ids) & {c.incident.incident_id for c in candidates}
    ]
    if relevant_conflicts:
        confidence -= settings.conflict_confidence_penalty * len(relevant_conflicts)
        penalties.append(
            f"{len(relevant_conflicts)} unresolved conflicting remediation(s)"
        )
        for conflict in relevant_conflicts:
            unknowns.append(
                f"Historical guidance on {conflict.target} conflicts: {conflict.summary} "
                f"{conflict.distinguishing_question}"
            )

    if not runbooks:
        confidence *= 0.9
        penalties.append("no runbook matched")
        unknowns.append("No runbook matched this trigger, so there is no documented procedure to follow.")

    confidence = max(0.0, min(1.0, confidence))
    # A case only supports the leading hypothesis if it scores near the top one.
    # Everything above the bare threshold is worth showing, not worth counting.
    support_floor = max(settings.confidence_threshold, 0.8 * top.score)
    supporting = [c for c in candidates if c.score >= support_floor]

    if confidence < settings.confidence_threshold:
        reason = (
            f"Confidence {confidence:.2f} is below the {settings.confidence_threshold:.2f} "
            f"threshold ({'; '.join(penalties)})."
            if penalties
            else f"Confidence {confidence:.2f} is below the "
            f"{settings.confidence_threshold:.2f} threshold; no case matches closely enough."
        )
        return GateDecision(
            mode=ResponseMode.ESCALATION,
            confidence=confidence,
            reason=reason,
            unknowns=tuple(unknowns or (_weak_match_unknown(top),)),
            escalate_to=_escalation_target(runbooks),
        )

    if len(supporting) < settings.min_supporting_cases:
        return GateDecision(
            mode=ResponseMode.ESCALATION,
            confidence=confidence,
            reason=(
                f"Only {len(supporting)} case scored above the threshold; "
                f"{settings.min_supporting_cases} required to propose a cause."
            ),
            unknowns=tuple(unknowns or (_weak_match_unknown(top),)),
            escalate_to=_escalation_target(runbooks),
        )

    reason = (
        f"{top.incident.incident_id} matches at {top.score:.2f} on the same service and "
        f"environment; {len(supporting)} supporting case(s)."
    )
    if penalties:
        reason += f" Confidence reduced to {confidence:.2f}: {'; '.join(penalties)}."

    return GateDecision(
        mode=ResponseMode.INVESTIGATION,
        confidence=confidence,
        reason=reason,
        unknowns=tuple(unknowns),
        escalate_to=_escalation_target(runbooks),
    )


def _escalation_target(runbooks: list[RunbookMatch]) -> str | None:
    for match in runbooks:
        if match.runbook.escalate_to:
            return match.runbook.escalate_to
    return None


def _weak_match_unknown(top: CandidateCase) -> str:
    return (
        f"The closest case, {top.incident.incident_id}, matches at only "
        f"{top.score:.2f}; nothing in the corpus explains these symptoms."
    )
