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
from dataclasses import replace
from datetime import datetime
from typing import Any

from app.analysis.evidence import EvidencePack
from app.analysis.temporal import describe
from app.config import Settings
from app.db.repository import SqliteRepository
from app.domain import CurrentIncident, ResponseMode, RootCauseStatus
from app.llm import ChatMessage, LLMError, LLMProvider
from app.retrieval import HybridRetriever
from app.services import faithfulness, prompts
from app.services.reports import (
    EscalationReport,
    InvestigationResult,
    InvestigationStep,
    conflict_lines,
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
        result = self._finalise(pack, draft, list(notes))
        if not (draft and self._lost_every_step(pack, result)):
            return result

        # The evidence carried an investigation and only the draft's form lost
        # it, so the model gets the reasons once. The second draft goes through
        # the same checks; if it fails too, the escalation stands.
        reasons = [n for n in result.validation_notes if n.startswith("Dropped")]
        retry, retry_notes = await self._generate(pack, previous=draft, reasons=reasons)
        if not retry:
            return result
        second = self._finalise(pack, retry, [*notes, *retry_notes])
        if not second.steps:
            return replace(
                result,
                validation_notes=(
                    *result.validation_notes,
                    "The model was shown these reasons and asked once more; the "
                    "second draft lost every step too, so the escalation stands.",
                ),
            )
        return replace(
            second,
            validation_notes=(
                f"Every step in the first draft was dropped ({len(reasons)} reason(s)); "
                "the model was shown the reasons and asked once more.",
                *second.validation_notes,
            ),
        )

    def _lost_every_step(self, pack: EvidencePack, result: InvestigationResult) -> bool:
        return (
            self.settings.repair_attempts > 0
            and pack.mode is ResponseMode.INVESTIGATION
            and result.mode is ResponseMode.ESCALATION
            and not result.steps
        )

    async def _generate(
        self,
        pack: EvidencePack,
        *,
        previous: dict[str, Any] | None = None,
        reasons: list[str] | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        messages = [
            ChatMessage(role="system", content=prompts.SYSTEM_PROMPT),
            ChatMessage(
                role="user",
                content=prompts.build_user_prompt(pack, max_steps=self.settings.max_steps),
            ),
        ]
        if previous is not None:
            messages += [
                ChatMessage(role="assistant", content=json.dumps(previous)),
                ChatMessage(role="user", content=prompts.build_repair_prompt(reasons or [])),
            ]
        try:
            raw = await self.provider.chat(
                messages, json_schema=prompts.response_schema(pack), temperature=0.0
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
        raw_hypothesis = _text(draft.get("root_cause_hypothesis"))

        # Past "the id was retrieved": does the cited text say it, does the plan
        # pick a side of a conflict, is the cause grounded in a supporting case.
        checked = faithfulness.check(
            pack,
            steps=steps,
            hypothesis=raw_hypothesis if pack.mode is ResponseMode.INVESTIGATION else None,
            cautions=_strings(draft.get("cautions")),
            unresolved=_strings(draft.get("unresolved")),
            max_steps=self.settings.max_steps,
            citation_support_min=self.settings.citation_support_min,
            hypothesis_grounding_min=self.settings.hypothesis_grounding_min,
            investigation=pack.mode is ResponseMode.INVESTIGATION,
        )
        notes.extend(checked.notes)
        steps = checked.steps
        hypothesis = (
            checked.hypothesis if pack.mode is ResponseMode.INVESTIGATION else raw_hypothesis
        )
        removed_by_checks = bool(raw_hypothesis) and not hypothesis
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
            if not removed_by_checks:
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

        basis = checked.hypothesis_basis
        if mode is ResponseMode.ESCALATION:
            status, hypothesis, basis = RootCauseStatus.UNDETERMINED, None, ()

        unresolved = _merge(pack.unknowns, checked.unresolved)
        cautions = checked.cautions
        headline = _text(draft.get("headline"))
        if headline and _looks_like_title(headline):
            notes.append(
                f"The model's headline \"{_clip(headline)}\" was a title, not a "
                "statement; replaced with one built from the evidence."
            )
            headline = None
        headline = headline or _fallback_headline(pack)
        if mode is not pack.mode:
            # The model's headline was written for a mode the checks overturned.
            headline = _fallback_headline(pack, mode)

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
            hypothesis_basis=basis,
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

        cited = (
            _strings(entry.get("cases"))
            + _strings(entry.get("runbooks"))
            + _strings(entry.get("citations"))
        )
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
                would_falsify=_text(entry.get("would_falsify")) or "",
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
    for filter_name in ("environment", "region"):
        dropped = [e.incident_id for e in pack.excluded if e.filter == filter_name]
        if dropped:
            evidence.append(f"Excluded by {filter_name} filter: " + ", ".join(dropped[:5]))
    if pack.support:
        evidence.append(
            "Cases sharing the closest match's failure shape: "
            + ", ".join(s.incident_id for s in pack.support)
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
        conflicting_guidance=conflict_lines(pack),
    )


def _fallback_headline(pack: EvidencePack, mode: ResponseMode | None = None) -> str:
    current = pack.current
    if (mode or pack.mode) is ResponseMode.ESCALATION:
        return (
            f"{current.service} in {current.environment.value}: root cause "
            "undetermined from the available evidence."
        )
    top = pack.top
    return (
        f"{current.service} in {current.environment.value} resembles "
        f"{top.incident.incident_id}: {top.incident.title}."
        if top
        else f"{current.service} in {current.environment.value}."
    )


_MINOR_WORDS = frozenset({"a", "an", "and", "the", "of", "for", "in", "on", "to", "with", "after"})
_HEADING_NOUNS = frozenset({"analysis", "report", "summary", "overview", "investigation", "resolution"})


def _looks_like_title(text: str) -> bool:
    """Unpunctuated and either Capitalised Like A Heading or named like a document."""
    words = text.split()
    if text.rstrip().endswith((".", "!", "?")) or len(words) > 16:
        return False
    if any(w.lower().strip(":,") in _HEADING_NOUNS for w in words):
        return True
    major = [w for w in words if w.lower() not in _MINOR_WORDS]
    return bool(major) and all(w[0].isupper() or not w[0].isalpha() for w in major)


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
