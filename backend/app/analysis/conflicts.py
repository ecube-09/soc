"""Conflicting-fix detection.

Two past incidents can point at the same knob and disagree about which way to
turn it. Repeating either one as advice is how a retrieval system causes an
outage, so the pair is surfaced as a conflict with the question that decides
between them.

This reads the structured resolution actions, not the prose, so a conflict is a
fact about the corpus rather than something the model noticed.
"""

from dataclasses import dataclass
from itertools import combinations

from app.domain import Incident, ResolutionAction, directions_conflict


@dataclass(frozen=True)
class ConflictSide:
    incident_id: str
    title: str
    action: ResolutionAction
    root_cause: str
    age_days: int

    def to_dict(self) -> dict[str, object]:
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "action": self.action.describe(),
            "direction": self.action.direction.value,
            "root_cause": self.root_cause,
            "age_days": self.age_days,
        }


@dataclass(frozen=True)
class ConflictingFix:
    target: str
    component: str
    summary: str
    distinguishing_question: str
    more_recent_incident_id: str
    sides: tuple[ConflictSide, ConflictSide]

    @property
    def incident_ids(self) -> tuple[str, ...]:
        return tuple(side.incident_id for side in self.sides)

    def to_dict(self) -> dict[str, object]:
        return {
            "target": self.target,
            "component": self.component,
            "summary": self.summary,
            "distinguishing_question": self.distinguishing_question,
            "more_recent_incident_id": self.more_recent_incident_id,
            "sides": [side.to_dict() for side in self.sides],
        }


def detect(incidents: list[Incident], *, reference_day=None) -> list[ConflictingFix]:
    """Find pairs that moved the same knob on the same component opposite ways."""
    by_knob: dict[tuple[str, str], list[tuple[Incident, ResolutionAction]]] = {}
    for incident in incidents:
        for action in incident.actions:
            by_knob.setdefault((action.component, action.target), []).append(
                (incident, action)
            )

    latest = reference_day or max(
        (i.started_at for i in incidents), default=None
    )

    conflicts: list[ConflictingFix] = []
    for (component, target), entries in sorted(by_knob.items()):
        for (left_incident, left_action), (right_incident, right_action) in combinations(
            entries, 2
        ):
            if left_incident.incident_id == right_incident.incident_id:
                continue
            if not directions_conflict(left_action.direction, right_action.direction):
                continue

            older, newer = sorted(
                ((left_incident, left_action), (right_incident, right_action)),
                key=lambda entry: entry[0].started_at,
            )
            sides = (
                _side(older[0], older[1], latest),
                _side(newer[0], newer[1], latest),
            )
            conflicts.append(
                ConflictingFix(
                    target=target,
                    component=component,
                    summary=(
                        f"{sides[0].incident_id} applied "
                        f"{sides[0].action.describe()}; {sides[1].incident_id} later applied "
                        f"{sides[1].action.describe()}."
                    ),
                    distinguishing_question=(
                        f"Measure current {target} utilisation on {component} and establish "
                        f"whether the limit is being hit on {component} or on its dependency, "
                        f"before moving {target} either way."
                    ),
                    more_recent_incident_id=sides[1].incident_id,
                    sides=sides,
                )
            )
    return conflicts


def conflicts_for(
    incidents: list[Incident],
    candidate_ids: set[str],
    components: set[str],
) -> list[ConflictingFix]:
    """Conflicts this investigation could actually act on.

    Two filters, both needed. At least one side must be a case retrieval
    surfaced, because a disagreement nobody looked up is not this incident's
    problem. And the knob must sit on the current service or a dependency of it,
    because past disagreement about an unrelated component is noise that would
    otherwise depress confidence on every investigation.
    """
    return [
        conflict
        for conflict in detect(incidents)
        if candidate_ids & set(conflict.incident_ids)
        and conflict.component in components
    ]


def _side(incident: Incident, action: ResolutionAction, latest) -> ConflictSide:
    age_days = (latest - incident.started_at).days if latest else 0
    return ConflictSide(
        incident_id=incident.incident_id,
        title=incident.title,
        action=action,
        root_cause=incident.root_cause,
        age_days=age_days,
    )
