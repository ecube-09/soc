"""Checks that go past "the cited id was retrieved".

A citation id being in the evidence pack proves the document exists, not that it
says what the step claims. These checks look at the text:

- a citation whose document shares too little with the step is dropped;
- a step that proposes moving a knob past incidents disagree about is dropped,
  because the conflict panel exists precisely so nobody picks a side unmeasured;
- a step that merely restates a runbook step, with no historical case behind
  it, is dropped, since the runbook is already on screen;
- a hypothesis must be grounded in the recorded root cause of a supporting case
  and must name that case; remediation buried inside it is cut.

All lexical and conservative. They catch claims with no footing, not subtle
overstatement, and the README says so.
"""

import re
from dataclasses import dataclass, replace

from app.analysis.conflicts import ConflictingFix
from app.analysis.evidence import EvidencePack
from app.analysis.text import coverage, jaccard, mentions_knob, sentences
from app.services.reports import InvestigationStep

_INCREASE = (
    r"increase|increasing|raise|raising|bump|bumping|double|doubling|grow|growing|"
    r"enlarge|enlarging|scale[sd]? out|scaling out|scale[sd]? up|scaling up|"
    r"add(?:ing)? (?:more )?(?:\w+ )?replicas"
)
_DECREASE = (
    r"decrease|decreasing|reduce|reducing|lower|lowering|cut|cutting|shrink|"
    r"shrinking|scale[sd]? in|scaling in|scale[sd]? down|scaling down|"
    r"remov(?:e|ing) (?:\w+ )?replicas"
)
_CHANGE = r"set|setting|change|changing|adjust|adjusting|tune|tuning|resize|resizing|revert|reverting"

_INCREASE_RE = re.compile(rf"\b(?:{_INCREASE})\b", re.IGNORECASE)
_DECREASE_RE = re.compile(rf"\b(?:{_DECREASE})\b", re.IGNORECASE)
_CHANGE_RE = re.compile(rf"\b(?:{_CHANGE})\b", re.IGNORECASE)

_OBSERVE_FIRST_WORDS = frozenset(
    """
    check measure read compare inspect confirm verify review look query examine
    determine establish correlate pull graph grep watch monitor capture collect
    record note identify list count chart plot trace sample see find
    """.split()
)

_ID = re.compile(r"\b(?:PM-EXT|INC|PM|RB|LOG|EXT)-\d{3,5}\b")
_HISTORICAL_PREFIXES = ("INC-", "PM-", "LOG-", "EXT-")


@dataclass(frozen=True)
class FaithfulnessOutcome:
    steps: tuple[InvestigationStep, ...]
    hypothesis: str | None
    hypothesis_basis: tuple[str, ...]
    cautions: tuple[str, ...]
    unresolved: tuple[str, ...]
    notes: tuple[str, ...]


def check(
    pack: EvidencePack,
    *,
    steps: tuple[InvestigationStep, ...],
    hypothesis: str | None,
    cautions: tuple[str, ...],
    unresolved: tuple[str, ...],
    max_steps: int,
    citation_support_min: float,
    hypothesis_grounding_min: float,
    investigation: bool,
) -> FaithfulnessOutcome:
    notes: list[str] = []

    steps = _check_step_content(pack, steps, citation_support_min, notes)
    steps = _drop_disputed_remediation(pack, steps, notes)
    if investigation:
        steps = _drop_runbook_transcription(pack, steps, notes)
    steps = _order_and_cap(steps, max_steps, notes)

    basis: tuple[str, ...] = ()
    if hypothesis:
        hypothesis, basis = _check_hypothesis(
            pack, hypothesis, hypothesis_grounding_min, notes
        )

    cautions = _drop_conflict_restatements(pack.conflicts, cautions, "caution", notes)
    unresolved = _drop_conflict_restatements(pack.conflicts, unresolved, "unresolved point", notes)

    return FaithfulnessOutcome(
        steps=steps,
        hypothesis=hypothesis,
        hypothesis_basis=basis,
        cautions=cautions,
        unresolved=unresolved,
        notes=tuple(notes),
    )


def _check_step_content(
    pack: EvidencePack,
    steps: tuple[InvestigationStep, ...],
    minimum: float,
    notes: list[str],
) -> tuple[InvestigationStep, ...]:
    """Each citation's document has to share real vocabulary with the step."""
    texts = pack.source_texts()
    kept_steps: list[InvestigationStep] = []
    for step in steps:
        claim = f"{step.action} {step.rationale}"
        scored = {cid: coverage(claim, texts.get(cid, "")) for cid in step.citations}
        kept = tuple(cid for cid in step.citations if scored[cid] >= minimum)
        dropped = [cid for cid in step.citations if cid not in kept]
        if dropped:
            notes.append(
                f"Dropped citation(s) {', '.join(dropped)} from \"{_clip(step.action)}\": "
                "the cited document does not mention what the step claims."
            )
        if not kept:
            notes.append(
                f"Dropped the step \"{_clip(step.action)}\": none of its sources "
                "support it."
            )
            continue
        kept_steps.append(
            replace(step, citations=kept, support=round(max(scored[c] for c in kept), 2))
        )
    return tuple(kept_steps)


def proposes_change(text: str, target: str) -> str | None:
    """'increase', 'decrease', or 'change' if the text proposes moving the knob.

    Past tense ("the pool was increased") describes history and is not matched,
    and a step that opens with an observation verb is a measurement, even if it
    mentions what the measurement would decide.
    """
    first = text.strip().split(" ", 1)[0].lower().strip(",.:;") if text.strip() else ""
    if first in _OBSERVE_FIRST_WORDS:
        return None
    for sentence in sentences(text):
        if not mentions_knob(sentence, target):
            continue
        if _INCREASE_RE.search(sentence):
            return "increase"
        if _DECREASE_RE.search(sentence):
            return "decrease"
        if _CHANGE_RE.search(sentence):
            return "change"
    return None


def _drop_disputed_remediation(
    pack: EvidencePack, steps: tuple[InvestigationStep, ...], notes: list[str]
) -> tuple[InvestigationStep, ...]:
    ruled_out = _ruled_out_citations(pack)
    kept: list[InvestigationStep] = []
    for step in steps:
        verdict = _disputed(pack, step.action)
        if verdict:
            notes.append(f"Dropped the step \"{_clip(step.action)}\": {verdict}")
            continue
        historical = {c for c in step.citations if c.startswith(_HISTORICAL_PREFIXES)}
        if historical and historical <= ruled_out.keys():
            rejected = sorted({ruled_out[c] for c in historical})
            notes.append(
                f"Dropped the step \"{_clip(step.action)}\": it rests only on "
                f"{', '.join(rejected)}, which a recorded measurement ruled out."
            )
            continue
        if step.would_falsify and _disputed(pack, step.would_falsify):
            notes.append(
                f"Cleared the falsification note on \"{_clip(step.action)}\": it "
                "proposed a disputed change instead of saying what would count "
                "against the hypothesis."
            )
            step = replace(step, would_falsify="")
        kept.append(step)
    return tuple(kept)


def _disputed(pack: EvidencePack, text: str) -> str | None:
    for conflict in pack.conflicts:
        direction = proposes_change(text, conflict.target)
        if direction is None:
            continue
        resolution = pack.resolution_for(conflict)
        if resolution is None:
            return (
                f"it proposes changing {conflict.target}, which past incidents "
                "disagree about. Measure first; see the conflicting guidance."
            )
        rejected = next(
            s for s in conflict.sides if s.incident_id == resolution.rejected_incident_id
        )
        if _same_way(direction, rejected.action.direction.value):
            return (
                f"it moves {conflict.target} the way {rejected.incident_id} did, and the "
                f"recorded measurement favours {resolution.favours_incident_id}."
            )
    return None


def _ruled_out_citations(pack: EvidencePack) -> dict[str, str]:
    """Every id (incident, postmortem, log) belonging to a side a finding rejected."""
    rejected = {
        resolution.rejected_incident_id
        for conflict in pack.conflicts
        if (resolution := pack.resolution_for(conflict))
    }
    return {
        cid: candidate.incident.incident_id
        for candidate in pack.candidates
        if candidate.incident.incident_id in rejected
        for cid in candidate.citation_ids()
    }


def _same_way(direction: str, action_direction: str) -> bool:
    ups = {"increase", "scale_out"}
    downs = {"decrease", "scale_in"}
    return (direction == "increase" and action_direction in ups) or (
        direction == "decrease" and action_direction in downs
    )


def _drop_runbook_transcription(
    pack: EvidencePack, steps: tuple[InvestigationStep, ...], notes: list[str]
) -> tuple[InvestigationStep, ...]:
    """An investigation step rests on a past case; runbooks are shown on their own."""
    runbooks = {m.runbook.runbook_id: m.runbook for m in pack.runbooks}
    kept: list[InvestigationStep] = []
    for step in steps:
        if _has_historical(step):
            kept.append(step)
            continue
        restated = next(
            (
                cid
                for cid in step.citations
                if cid in runbooks
                and any(jaccard(step.action, line) >= 0.5 for line in runbooks[cid].steps)
            ),
            None,
        )
        if restated:
            notes.append(
                f"Dropped the step \"{_clip(step.action)}\": it restates {restated} "
                "without a historical case behind it. The runbook is shown separately."
            )
        else:
            notes.append(
                f"Dropped the step \"{_clip(step.action)}\": it cites no historical "
                "case, and a step here has to say which past incident it comes from."
            )
    return tuple(kept)


def _order_and_cap(
    steps: tuple[InvestigationStep, ...], max_steps: int, notes: list[str]
) -> tuple[InvestigationStep, ...]:
    ordered = sorted(steps, key=lambda s: 0 if _has_historical(s) else 1)
    if len(ordered) > max_steps:
        notes.append(
            f"Kept the first {max_steps} of {len(ordered)} steps, preferring those "
            "backed by a historical case."
        )
    return tuple(ordered[:max_steps])


def _has_historical(step: InvestigationStep) -> bool:
    return any(c.startswith(_HISTORICAL_PREFIXES) for c in step.citations)


def _check_hypothesis(
    pack: EvidencePack, hypothesis: str, minimum: float, notes: list[str]
) -> tuple[str | None, tuple[str, ...]]:
    allowed = pack.citation_ids()
    invented = sorted({i for i in _ID.findall(hypothesis) if i not in allowed})
    if invented:
        notes.append(
            f"The hypothesis named {', '.join(invented)}, which was not retrieved. "
            "It was removed."
        )
        return None, ()

    kept_sentences = []
    for sentence in sentences(hypothesis):
        verdict = _disputed(pack, sentence)
        if verdict:
            notes.append(
                "Removed a remediation from the hypothesis: "
                f"\"{_clip(sentence)}\" - {verdict}"
            )
            continue
        kept_sentences.append(sentence)
    hypothesis = " ".join(kept_sentences).strip()
    if not hypothesis:
        return None, ()

    grounding = _grounding(pack, hypothesis)
    if not grounding:
        return None, ()
    best_id, best = max(grounding.items(), key=lambda kv: kv[1])
    if best < minimum:
        notes.append(
            "The hypothesis has no footing in the root cause recorded for any "
            f"supporting case (best overlap {best:.0%}, with {best_id}). It was removed."
        )
        return None, ()

    # The closest match is the anchor whenever it grounds the claim at all; a
    # hypothesis that only fits a lower-ranked case is kept but said to be so.
    top_id = pack.supporting_ids[0]
    anchor = top_id if grounding.get(top_id, 0.0) >= minimum else best_id
    basis = tuple(
        sorted(
            (i for i, v in grounding.items() if v >= minimum),
            key=lambda i: (i != anchor, -grounding[i]),
        )
    )
    if anchor != top_id:
        notes.append(
            f"The hypothesis follows {anchor} rather than the closest match {top_id}."
        )
    named = set(_ID.findall(hypothesis))
    if not named & set(pack.supporting_ids):
        hypothesis = f"{hypothesis.rstrip('.')} (as recorded in {anchor})."
        notes.append(
            f"The hypothesis did not name the case it rests on; attributed to {anchor}."
        )
    return hypothesis, basis


def _grounding(pack: EvidencePack, hypothesis: str) -> dict[str, float]:
    """How much of the hypothesis each supporting case's recorded cause covers."""
    by_id = {c.incident.incident_id: c for c in pack.candidates}
    out: dict[str, float] = {}
    for incident_id in pack.supporting_ids:
        candidate = by_id.get(incident_id)
        if candidate is None:
            continue
        incident = candidate.incident
        source = " ".join(
            [
                incident.title,
                incident.summary,
                incident.root_cause,
                *incident.symptoms,
                incident.service,
                *incident.related_services,
                incident_id,
                candidate.postmortem.root_cause if candidate.postmortem else "",
            ]
        )
        out[incident_id] = coverage(hypothesis, source)
    return out


_CONFLICT_WORDS = re.compile(
    r"\b(conflict\w*|contradict\w*|disagree\w*|opposite|oppos\w+|revers\w+|inconsistent"
    r"|settled|in favou?r of|ruled out)\b",
    re.IGNORECASE,
)


def restates_conflict(text: str, conflicts: tuple[ConflictingFix, ...]) -> bool:
    for conflict in conflicts:
        ids = set(conflict.incident_ids)
        named = set(_ID.findall(text))
        if ids <= named:
            return True
        if _CONFLICT_WORDS.search(text) and (
            mentions_knob(text, conflict.target) or named & ids
        ):
            return True
    return False


def _drop_conflict_restatements(
    conflicts: tuple[ConflictingFix, ...],
    items: tuple[str, ...],
    label: str,
    notes: list[str],
) -> tuple[str, ...]:
    if not conflicts:
        return items
    kept = tuple(item for item in items if not restates_conflict(item, conflicts))
    removed = len(items) - len(kept)
    if removed:
        notes.append(
            f"Removed {removed} {label}{'s' if removed > 1 else ''} that restated the "
            "conflicting guidance, which is shown once in the conflict panel."
        )
    return kept


def _clip(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"
