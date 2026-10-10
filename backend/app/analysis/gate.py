"""The confidence gate.

This is the rule the project turns on: when the evidence does not support naming
a cause, the system escalates instead of guessing. It runs twice - once on the
retrieved evidence, before a single token is generated, and again on the model's
output to check that what it claimed is actually cited.

It is code rather than prompt text because a prompt is a request and this is a
requirement.
"""

from dataclasses import dataclass, field

from app.analysis.conflicts import ConflictingFix
from app.analysis.evidence import CandidateCase, RunbookMatch
from app.analysis.scoring import failure_shape
from app.config import Settings
from app.domain import Finding, ResponseMode


@dataclass(frozen=True)
class ConflictResolution:
    """A conflict a person settled with a measurement. The gate only applies it."""

    target: str
    component: str
    favours_incident_id: str
    rejected_incident_id: str
    observation: str
    author: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "target": self.target,
            "component": self.component,
            "favours_incident_id": self.favours_incident_id,
            "rejected_incident_id": self.rejected_incident_id,
            "observation": self.observation,
            "author": self.author,
        }


@dataclass(frozen=True)
class Support:
    incident_id: str
    reason: str
    stale: bool = False

    def to_dict(self) -> dict[str, object]:
        return {"incident_id": self.incident_id, "reason": self.reason, "stale": self.stale}


@dataclass(frozen=True)
class GateDecision:
    mode: ResponseMode
    confidence: float
    reason: str
    unknowns: tuple[str, ...]
    escalate_to: str | None
    support: tuple[Support, ...] = ()
    resolutions: tuple[ConflictResolution, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def supporting_ids(self) -> tuple[str, ...]:
        return tuple(s.incident_id for s in self.support)


def evaluate(
    *,
    has_timeline: bool,
    candidates: list[CandidateCase],
    runbooks: list[RunbookMatch],
    conflicts: list[ConflictingFix],
    settings: Settings,
    findings: tuple[Finding, ...] = (),
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
    top_id = top.incident.incident_id
    confidence = top.score
    unknowns: list[str] = []
    penalties: list[str] = []
    notes: list[str] = []

    if top.is_stale:
        confidence *= 0.6
        penalties.append("closest case predates the current stack")
        unknowns.append(
            f"{top_id} is {top.age_days} days old; its remediation "
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

    # The conflict itself is stated once, in the conflict list. The gate only
    # charges for it here, so it is not repeated as an unknown.
    candidate_ids = {c.incident.incident_id for c in candidates}
    relevant = [c for c in conflicts if set(c.incident_ids) & candidate_ids]
    resolutions, unresolved, finding_notes = _apply_findings(relevant, findings)
    notes.extend(finding_notes)

    if unresolved:
        confidence -= settings.conflict_confidence_penalty * len(unresolved)
        penalties.append(f"{len(unresolved)} unresolved conflicting remediation(s)")

    rejected = {r.rejected_incident_id: r for r in resolutions}
    if top_id in rejected:
        resolution = rejected[top_id]
        confidence *= settings.contradicted_top_factor
        penalties.append(
            f"a recorded measurement favours {resolution.favours_incident_id} over {top_id}"
        )
        unknowns.append(
            f"The measurement \"{resolution.observation}\" favours "
            f"{resolution.favours_incident_id} on {resolution.target}, not the closest "
            f"case {top_id}. The leading match does not fit what was measured; "
            f"{resolution.favours_incident_id} may describe this incident better but "
            "ranks lower on the reported symptoms."
        )

    if not runbooks:
        confidence *= 0.9
        penalties.append("no runbook matched")
        unknowns.append("No runbook matched this trigger, so there is no documented procedure to follow.")

    confidence = max(0.0, min(1.0, confidence))
    support = _support(top, candidates, set(rejected), settings)

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
            support=support,
            resolutions=resolutions,
            notes=tuple(notes),
        )

    if len(support) < settings.min_supporting_cases:
        return GateDecision(
            mode=ResponseMode.ESCALATION,
            confidence=confidence,
            reason=(
                f"Only {len(support)} case supports the hypothesis; "
                f"{settings.min_supporting_cases} required to propose a cause."
            ),
            unknowns=tuple(unknowns or (_weak_match_unknown(top),)),
            escalate_to=_escalation_target(runbooks),
            support=support,
            resolutions=resolutions,
            notes=tuple(notes),
        )

    reason = f"{top_id} matches at {top.score:.2f}{_where(top)}; {_support_phrase(support)}."
    for resolution in resolutions:
        reason += (
            f" The conflict on {resolution.target} was settled by a recorded "
            f"measurement in favour of {resolution.favours_incident_id}."
        )
    if penalties:
        reason += f" Confidence reduced to {confidence:.2f}: {'; '.join(penalties)}."

    return GateDecision(
        mode=ResponseMode.INVESTIGATION,
        confidence=confidence,
        reason=reason,
        unknowns=tuple(unknowns),
        escalate_to=_escalation_target(runbooks),
        support=support,
        resolutions=resolutions,
        notes=tuple(notes),
    )


def _apply_findings(
    conflicts: list[ConflictingFix], findings: tuple[Finding, ...]
) -> tuple[tuple[ConflictResolution, ...], list[ConflictingFix], list[str]]:
    """Match each conflict to the latest finding recorded on the same knob."""
    resolutions: list[ConflictResolution] = []
    unresolved: list[ConflictingFix] = []
    notes: list[str] = []

    for conflict in conflicts:
        matching = [
            f
            for f in findings
            if f.target == conflict.target and f.component == conflict.component
        ]
        if not matching:
            unresolved.append(conflict)
            continue

        finding = matching[-1]
        if finding.favours_incident_id not in conflict.incident_ids:
            notes.append(
                f"A finding on {conflict.target} favours {finding.favours_incident_id}, "
                "which is not a side of this conflict, so it was not applied."
            )
            unresolved.append(conflict)
            continue

        rejected = next(
            i for i in conflict.incident_ids if i != finding.favours_incident_id
        )
        resolutions.append(
            ConflictResolution(
                target=conflict.target,
                component=conflict.component,
                favours_incident_id=finding.favours_incident_id,
                rejected_incident_id=rejected,
                observation=finding.observation,
                author=finding.author,
            )
        )

    return tuple(resolutions), unresolved, notes


def _support(
    top: CandidateCase,
    candidates: list[CandidateCase],
    rejected: set[str],
    settings: Settings,
) -> tuple[Support, ...]:
    """Cases that agree with the leading hypothesis, not just its neighbours.

    A case counts only if it scores close to the top case and shares its failure
    shape. Another payment-api incident with a different cause is a neighbour,
    and counting it would overstate how much the history agrees.
    """
    floor = max(settings.confidence_threshold, settings.support_score_ratio * top.score)
    support = [Support(top.incident.incident_id, "closest match", top.is_stale)]
    for candidate in candidates[1:]:
        if candidate.incident.incident_id in rejected or candidate.score < floor:
            continue
        shape = failure_shape(
            top.incident,
            candidate.incident,
            min_similarity=settings.support_min_shape_similarity,
        )
        if shape.shared:
            support.append(
                Support(candidate.incident.incident_id, shape.describe(), candidate.is_stale)
            )
    return tuple(support)


def _support_phrase(support: tuple[Support, ...]) -> str:
    others = support[1:]
    if not others:
        return "no other case shares its failure shape"
    names = ", ".join(f"{s.incident_id}{' (stale)' if s.stale else ''}" for s in others)
    return f"{len(others)} other case(s) share its failure shape: {names}"


def _where(top: CandidateCase) -> str:
    if "same-service" in top.flags:
        return " on the same service and environment"
    if top.graph_reason:
        return f" ({top.graph_reason})"
    return ""


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
