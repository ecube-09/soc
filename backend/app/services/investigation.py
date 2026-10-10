"""Generation, validation, and the second pass of the gate.

One model call, constrained to a JSON schema, fed only the evidence pack. What
comes back is then checked rather than trusted: citations that name ids nobody
retrieved are dropped, steps left with no support are dropped, and a root cause
asserted on evidence the gate already rejected is removed.

If the checks leave nothing standing, the answer becomes an escalation report.
That is the point of the system: it would rather hand back an honest "we could
not determine this" than a confident guess.
"""

import json
import logging
from datetime import datetime
from typing import Any

from app.analysis.evidence import EvidencePack
from app.analysis.temporal import describe
from app.config import Settings
from app.db.repository import SqliteRepository
from app.domain import CurrentIncident, ResponseMode, RootCauseStatus
from app.llm import ChatMessage, LLMError, LLMProvider
from app.retrieval import HybridRetriever
from app.services import prompts
from app.services.reports import (
    EscalationReport,
    InvestigationResult,
    InvestigationStep,
)

logger = logging.getLogger(__name__)

DEFAULT_TEAM = "service owning team"


class InvestigationService:
    def __init__(
        self,
        retriever: HybridRetriever,
        provider: LLMProvider,
        repo: SqliteRepository,
        settings: Settings,
    ) -> None:
        self.retriever = retriever
        self.provider = provider
        self.repo = repo
        self.settings = settings

    async def investigate(
        self, current: CurrentIncident, *, now: datetime | None = None
    ) -> InvestigationResult:
        pack = await self.retriever.build_evidence(current, now=now)
        return await self.explain(pack)

    async def explain(self, pack: EvidencePack) -> InvestigationResult:
        draft, notes = await self._generate(pack)
        return self._finalise(pack, draft, notes)

    async def _generate(
        self, pack: EvidencePack
    ) -> tuple[dict[str, Any], list[str]]:
        messages = [
            ChatMessage(role="system", content=prompts.SYSTEM_PROMPT),
            ChatMessage(role="user", content=prompts.build_user_prompt(pack)),
        ]
        try:
            raw = await self.provider.chat(
                messages, json_schema=prompts.RESPONSE_SCHEMA, temperature=0.0
            )
        except LLMError as exc:
            logger.warning("generation failed, falling back to the report: %s", exc)
            return {}, [
                "The model runtime did not respond, so this report was assembled "
                "from the retrieved evidence alone."
            ]

        try:
            draft = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("generation returned unparseable JSON")
            return {}, [
                "The model returned output that did not parse, so this report was "
                "assembled from the retrieved evidence alone."
            ]

        if not isinstance(draft, dict):
            return {}, ["The model returned an unexpected shape; output discarded."]
        return draft, []

    def _finalise(
        self, pack: EvidencePack, draft: dict[str, Any], notes: list[str]
    ) -> InvestigationResult:
        allowed = pack.citation_ids()
        steps, step_notes = _validate_steps(draft.get("steps"), allowed)
        notes.extend(step_notes)

        claimed = _read_status(draft)
        hypothesis = _text(draft.get("root_cause_hypothesis"))
        mode = pack.mode

        # The gate runs again here, on what the model actually produced. The
        # model's own label is advisory in both directions: it may not assert a
        # cause the evidence does not carry, and it may not withdraw one the
        # evidence does, because otherwise "undetermined" becomes a mood rather
        # than a statement about the evidence.
        if pack.mode is ResponseMode.ESCALATION:
            if claimed is RootCauseStatus.HYPOTHESIS or hypothesis:
                notes.append(
                    "The model proposed a root cause, but the evidence did not "
                    "support one. The cause was removed and this became an "
                    "escalation report."
                )
            status, hypothesis = RootCauseStatus.UNDETERMINED, None
        elif hypothesis:
            status = RootCauseStatus.HYPOTHESIS
            if claimed is RootCauseStatus.UNDETERMINED:
                notes.append(
                    "The model labelled its own answer undetermined while still "
                    "stating a cause. Recorded as a hypothesis, which is what an "
                    "unverified cause backed by comparable cases is."
                )
        else:
            status = RootCauseStatus.UNDETERMINED
            notes.append(
                "The evidence supported a hypothesis but none was stated, so "
                "nothing is being claimed."
            )

        if mode is ResponseMode.INVESTIGATION and not steps:
            notes.append(
                "No recommended step survived citation checking, so there is "
                "nothing evidence-backed to propose. Escalating instead."
            )
            mode = ResponseMode.ESCALATION

        if mode is ResponseMode.INVESTIGATION and status is RootCauseStatus.UNDETERMINED:
            notes.append(
                "No cause could be stated even though comparable cases were found; "
                "escalating with the evidence attached."
            )
            mode = ResponseMode.ESCALATION

        if mode is ResponseMode.ESCALATION:
            status, hypothesis = RootCauseStatus.UNDETERMINED, None

        unresolved = _merge(pack.unknowns, _strings(draft.get("unresolved")))
        cautions = _strings(draft.get("cautions"))
        headline = _text(draft.get("headline")) or _fallback_headline(pack)

        escalation = None
        if mode is ResponseMode.ESCALATION:
            escalation = _build_escalation(
                pack,
                steps=steps,
                unresolved=unresolved,
                requested_team=_text(draft.get("requested_team")),
            )

        return InvestigationResult(
            mode=mode,
            root_cause_status=status,
            confidence=pack.confidence,
            gate_reason=pack.gate_reason,
            headline=headline,
            root_cause_hypothesis=hypothesis,
            steps=steps,
            cautions=cautions,
            unresolved=unresolved,
            evidence=pack,
            escalation=escalation,
            validation_notes=tuple(notes),
        )


def _validate_steps(
    raw: Any, allowed: frozenset[str]
) -> tuple[tuple[InvestigationStep, ...], list[str]]:
    """Keep only steps that cite evidence which was actually retrieved."""
    if not isinstance(raw, list):
        return (), []

    steps: list[InvestigationStep] = []
    notes: list[str] = []

    for entry in raw:
        if not isinstance(entry, dict):
            continue
        action = _text(entry.get("action"))
        if not action:
            continue

        cited = _strings(entry.get("citations"))
        kept = tuple(dict.fromkeys(c for c in cited if c in allowed))
        invented = [c for c in cited if c not in allowed]
        if invented:
            notes.append(
                f"Dropped citation(s) {', '.join(sorted(set(invented)))} from "
                f"\"{_clip(action)}\": not in the retrieved evidence."
            )

        if not kept:
            notes.append(
                f"Dropped the step \"{_clip(action)}\": no valid citation remained."
            )
            continue

        steps.append(
            InvestigationStep(
                action=action,
                rationale=_text(entry.get("rationale")) or "",
                citations=kept,
            )
        )

    return tuple(steps), notes


def _build_escalation(
    pack: EvidencePack,
    *,
    steps: tuple[InvestigationStep, ...],
    unresolved: tuple[str, ...],
    requested_team: str | None,
) -> EscalationReport:
    current = pack.current

    timeline = tuple(
        f"{event.at:%H:%M}  {event.type.value}  [{event.service}]  {event.description}"
        for event in current.events
    ) or ("No timeline was supplied with this incident.",)

    correlation = tuple(
        f"{c.incident.incident_id} matches at {c.score:.2f} "
        f"({', '.join(c.metadata_reasons) or 'no metadata agreement'})"
        f"{' — STALE, predates the current stack' if c.is_stale else ''}"
        for c in pack.candidates
    ) or ("No historical case matched this service and environment.",)

    evidence: list[str] = []
    for candidate in pack.candidates:
        evidence.append(
            f"[{candidate.incident.incident_id}] {candidate.incident.title}: "
            f"{describe(candidate.timeline)}"
        )
    for match in pack.runbooks:
        evidence.append(
            f"[{match.runbook.runbook_id}] {match.runbook.title} "
            f"(trigger: {match.runbook.trigger})"
        )
    for conflict in pack.conflicts:
        evidence.append(f"Conflicting guidance on {conflict.target}: {conflict.summary}")
    if pack.excluded:
        evidence.append(
            "Excluded by environment filter: "
            + ", ".join(e.incident_id for e in pack.excluded[:5])
        )
    # Retrieval limitations belong in the report; the receiving team needs to
    # know what the search could not do, not just what it found.
    evidence.extend(pack.notes)

    next_checks = tuple(step.action for step in steps)
    if not next_checks:
        next_checks = tuple(
            step for match in pack.runbooks[:1] for step in match.runbook.steps[:3]
        ) or (
            "No documented procedure matched these symptoms. A human needs to "
            "establish a baseline before this can be narrowed further.",
        )

    return EscalationReport(
        incident_title=current.title,
        severity=current.severity.value,
        service=current.service,
        environment=current.environment.value,
        observed_symptoms=current.symptoms or (current.description,),
        timeline=timeline,
        historical_correlation=correlation,
        evidence_gathered=tuple(evidence) or ("Nothing relevant was retrieved.",),
        investigations_considered=steps,
        unresolved=unresolved
        or ("The root cause could not be determined from the available evidence.",),
        requested_team=requested_team or pack.escalate_to or DEFAULT_TEAM,
        recommended_next_investigation=next_checks,
        citations=tuple(sorted(pack.citation_ids())),
    )


def _fallback_headline(pack: EvidencePack) -> str:
    current = pack.current
    if pack.mode is ResponseMode.ESCALATION:
        return (
            f"{current.service} in {current.environment.value}: root cause "
            "undetermined from the available evidence."
        )
    top = pack.top
    return (
        f"{current.service} in {current.environment.value} resembles "
        f"{top.incident.incident_id}."
        if top
        else f"{current.service} in {current.environment.value}."
    )


def _read_status(draft: dict[str, Any]) -> RootCauseStatus:
    raw = _text(draft.get("root_cause_status"))
    try:
        return RootCauseStatus(raw) if raw else RootCauseStatus.UNDETERMINED
    except ValueError:
        return RootCauseStatus.UNDETERMINED


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(text for item in value if (text := _text(item)))


def _merge(*groups: tuple[str, ...]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for group in groups:
        for value in group:
            seen.setdefault(value, None)
    return tuple(seen)


def _clip(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"
