"""Prompt construction and the JSON contract for generation.

The model sees the evidence pack and nothing else, and it is told the exact set
of ids it may cite. Whether it obeys is not assumed: the validator checks.
"""

from app.analysis.evidence import EvidencePack
from app.analysis.temporal import describe
from app.domain import ResponseMode

SYSTEM_PROMPT = """\
You are an incident investigation assistant for an operations team.

You do not fix anything and you do not execute anything. You read evidence that
has already been retrieved and you propose what a human engineer should check
next. A recommendation to roll back or change a setting is a recommendation for a
person to consider, never an instruction that will be carried out.

Rules you must follow:

1. Use only the evidence provided in the EVIDENCE section. If a fact is not
   there, you do not know it. Never invent an incident id, a metric, a
   configuration value, a service, or a runbook step.
2. Every step you recommend must cite at least one id from the ALLOWED CITATIONS
   list, verbatim. Do not cite anything that is not on that list.
3. Root cause status is either "hypothesis" or "undetermined". There is no
   confirmed, because you cannot verify anything from here. "hypothesis" means
   the retrieved cases point at a likely cause; it does not claim certainty and
   it does not require that anything has been proven. "undetermined" means the
   evidence points nowhere, and it requires leaving the hypothesis field empty.
   Do not state a cause and then label it undetermined.
4. When the evidence contains conflicting historical remediations, do not pick a
   side. Say that the guidance conflicts and recommend the measurement that
   decides between them. A conflict about which fix to apply does not stop you
   from naming a likely cause: those are separate questions. Disagreement about
   the remedy is a caution, not an unknown cause.
5. When a historical case is flagged stale, you may use its diagnostic reasoning
   but must not repeat its specific remediation values as if they still apply.
6. Prefer checks that would distinguish between competing explanations over
   checks that merely confirm the leading one.
7. Write plainly, for an engineer mid-incident. No preamble.
"""

ESCALATION_INSTRUCTION = """\
The evidence has already been judged insufficient to name a root cause. Do not
name one. Your job here is narrower:

- Set root_cause_status to "undetermined" and leave root_cause_hypothesis empty.
- In "headline", state what is happening in one sentence using only the reported
  symptoms.
- In "steps", give the checks that would narrow this down, each cited. If the
  evidence supports no cited check at all, return an empty list.
- In "unresolved", state plainly what could not be determined and why.
"""

INVESTIGATION_INSTRUCTION = """\
Retrieval has already established that a comparable case exists and that the
evidence is strong enough to reason from. Do not re-litigate that. Produce:

- "headline": one sentence of prose on what this looks like and which case it
  resembles. Not a title, not a heading.
- "root_cause_status": "hypothesis". Use "undetermined" only if the comparable
  cases genuinely explain nothing about these symptoms.
- "root_cause_hypothesis": the likely cause, naming the case that suggests it.
- "steps": ordered checks, each with a rationale and citations. Put the check
  that would falsify the hypothesis early. Cite the historical case that makes a
  step relevant, not only the runbook the wording came from, and do not simply
  transcribe a runbook end to end.
- "cautions": anything that would make a step risky or misleading, including
  conflicting historical guidance and stale remediation.
- "unresolved": what remains unknown even if the hypothesis is right.
"""

RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "root_cause_status": {
            "type": "string",
            "enum": ["hypothesis", "undetermined"],
        },
        "root_cause_hypothesis": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "rationale": {"type": "string"},
                    "citations": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["action", "rationale", "citations"],
            },
        },
        "cautions": {"type": "array", "items": {"type": "string"}},
        "unresolved": {"type": "array", "items": {"type": "string"}},
        "requested_team": {"type": "string"},
    },
    "required": [
        "headline",
        "root_cause_status",
        "root_cause_hypothesis",
        "steps",
        "cautions",
        "unresolved",
    ],
}


def build_user_prompt(pack: EvidencePack) -> str:
    instruction = (
        ESCALATION_INSTRUCTION
        if pack.mode is ResponseMode.ESCALATION
        else INVESTIGATION_INSTRUCTION
    )
    return "\n".join(
        [
            instruction,
            "",
            "EVIDENCE",
            "========",
            _current_section(pack),
            _candidates_section(pack),
            _runbooks_section(pack),
            _conflicts_section(pack),
            _gate_section(pack),
            "",
            "ALLOWED CITATIONS",
            "=================",
            ", ".join(sorted(pack.citation_ids())) or "(none)",
        ]
    )


def _current_section(pack: EvidencePack) -> str:
    current = pack.current
    lines = [
        "CURRENT INCIDENT",
        "----------------",
        f"Title: {current.title}",
        f"Service: {current.service}   Environment: {current.environment.value}"
        f"   Severity: {current.severity.value}",
        f"Description: {current.description}",
    ]
    if current.symptoms:
        lines.append("Symptoms:")
        lines += [f"  - {s}" for s in current.symptoms]
    if current.suspected_dependencies:
        lines.append(
            f"Suspected dependencies: {', '.join(current.suspected_dependencies)}"
        )
    if current.events:
        lines.append("Timeline:")
        lines += [
            f"  {e.at:%H:%M} {e.type.value} [{e.service}] {e.description}"
            for e in current.events
        ]
    else:
        lines.append("Timeline: none provided.")
    if current.log_excerpt:
        lines.append("Log excerpt:")
        lines += [f"  {line}" for line in current.log_excerpt.splitlines()]
    return "\n".join(lines) + "\n"


def _candidates_section(pack: EvidencePack) -> str:
    if not pack.candidates:
        return (
            "COMPARABLE HISTORICAL CASES\n"
            "---------------------------\n"
            "None. No historical case survived retrieval and filtering.\n"
        )

    lines = ["COMPARABLE HISTORICAL CASES", "---------------------------"]
    for candidate in pack.candidates:
        incident = candidate.incident
        lines.append(
            f"[{incident.incident_id}] {incident.title}  "
            f"(match {candidate.score:.2f}, {candidate.age_days} days old)"
        )
        lines.append(
            f"  {incident.severity.value} · {incident.service} · "
            f"{incident.environment.value} · {incident.region}"
        )
        if candidate.flags:
            lines.append(f"  Flags: {', '.join(candidate.flags)}")
        if candidate.is_stale:
            lines.append(
                "  STALE: this case predates the current stack "
                f"({', '.join(incident.tech_context) or 'older platform'}). Its "
                "reasoning may transfer; its specific remediation values may not."
            )
        if candidate.metadata_reasons:
            lines.append(f"  Why it matched: {', '.join(candidate.metadata_reasons)}")
        lines.append(f"  Timeline: {describe(candidate.timeline)}")
        lines.append(f"  Root cause: {incident.root_cause}")
        lines.append(f"  Resolution: {incident.resolution}")
        if candidate.postmortem:
            lines.append(
                f"  [{candidate.postmortem.postmortem_id}] postmortem lessons:"
            )
            lines += [
                f"    - {lesson}" for lesson in candidate.postmortem.lessons_learned
            ]
        for log in candidate.logs:
            lines.append(f"  [{log.log_id}] {log.service} log excerpt:")
            lines += [f"    {line}" for line in log.lines]
        lines.append("")
    return "\n".join(lines)


def _runbooks_section(pack: EvidencePack) -> str:
    if not pack.runbooks:
        return (
            "RUNBOOKS\n--------\nNone matched this trigger.\n"
        )

    lines = ["RUNBOOKS", "--------"]
    for match in pack.runbooks:
        runbook = match.runbook
        lines.append(
            f"[{runbook.runbook_id}] {runbook.title} (v{runbook.version}, "
            f"updated {runbook.updated_at:%Y-%m-%d})"
        )
        lines.append(f"  Trigger: {runbook.trigger}")
        lines += [f"  {n}. {step}" for n, step in enumerate(runbook.steps, start=1)]
        if runbook.escalate_to:
            lines.append(f"  Escalate to: {runbook.escalate_to}")
        lines.append("")
    return "\n".join(lines)


def _conflicts_section(pack: EvidencePack) -> str:
    if not pack.conflicts:
        return "CONFLICTING HISTORICAL GUIDANCE\n-------------------------------\nNone.\n"

    lines = [
        "CONFLICTING HISTORICAL GUIDANCE",
        "-------------------------------",
        "Past incidents disagree about the following. Do not pick a side; "
        "recommend the measurement that decides.",
    ]
    for conflict in pack.conflicts:
        lines.append(f"- {conflict.target} on {conflict.component}: {conflict.summary}")
        for side in conflict.sides:
            lines.append(
                f"    [{side.incident_id}] {side.action.describe()} — {side.root_cause}"
            )
        lines.append(f"    Deciding check: {conflict.distinguishing_question}")
    lines.append("")
    return "\n".join(lines)


def _gate_section(pack: EvidencePack) -> str:
    lines = [
        "EVIDENCE ASSESSMENT",
        "-------------------",
        f"Confidence: {pack.confidence:.2f}",
        f"Assessment: {pack.gate_reason}",
    ]
    if pack.unknowns:
        lines.append("Known gaps:")
        lines += [f"  - {unknown}" for unknown in pack.unknowns]
    if pack.notes:
        lines += [f"  - {note}" for note in pack.notes]
    if pack.excluded:
        lines.append(
            "Deliberately excluded: "
            + "; ".join(f"{e.incident_id} ({e.reason})" for e in pack.excluded[:5])
        )
    return "\n".join(lines) + "\n"
