"""Non-semantic relevance signals: metadata, the service map, and age.

These exist because similar text does not mean similar incident. A staging case
with identical wording is the wrong case, and a four-year-old case with a perfect
match may carry remediation for a stack that no longer runs.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

from app.analysis.temporal import align
from app.domain import CurrentIncident, Incident, Runbook, Service

_SEVERITY_RANK = {"SEV-1": 1, "SEV-2": 2, "SEV-3": 3, "SEV-4": 4}

_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "was",
        "were",
        "with",
        "from",
        "that",
        "this",
        "into",
        "have",
        "has",
        "had",
        "are",
        "not",
        "but",
        "its",
        "after",
        "before",
        "over",
        "than",
        "then",
        "out",
        "any",
        "all",
        "rate",
        "baseline",
    }
)

_TOKEN = re.compile(r"[a-z0-9][a-z0-9_.-]{2,}")


@dataclass
class ServiceGraph:
    """One-hop neighbourhood lookups over the service map."""

    dependencies: dict[str, frozenset[str]] = field(default_factory=dict)
    dependents: dict[str, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def from_services(cls, services: list[Service]) -> "ServiceGraph":
        dependencies = {s.name: frozenset(s.dependencies) for s in services}
        dependents: dict[str, set[str]] = {s.name: set() for s in services}
        for service in services:
            for dependency in service.dependencies:
                dependents.setdefault(dependency, set()).add(service.name)
        return cls(
            dependencies=dependencies,
            dependents={name: frozenset(v) for name, v in dependents.items()},
        )

    def knows(self, service: str) -> bool:
        return service in self.dependencies

    def deps_of(self, service: str) -> frozenset[str]:
        return self.dependencies.get(service, frozenset())

    def dependents_of(self, service: str) -> frozenset[str]:
        return self.dependents.get(service, frozenset())

    def neighbourhood(self, service: str) -> frozenset[str]:
        return frozenset({service}) | self.deps_of(service) | self.dependents_of(service)


@dataclass(frozen=True)
class MetadataMatch:
    score: float
    reasons: tuple[str, ...]


def metadata_score(current: CurrentIncident, incident: Incident) -> MetadataMatch:
    """Agreement on the facts a retrieval system can check without a model."""
    score = 0.0
    reasons: list[str] = []

    if incident.service == current.service:
        score += 0.40
        reasons.append(f"same service ({incident.service})")

    if incident.environment == current.environment:
        score += 0.15
        reasons.append(f"same environment ({incident.environment.value})")

    shared_deps = set(current.suspected_dependencies) & (
        {incident.service, *incident.related_services}
    )
    if shared_deps:
        score += 0.20
        reasons.append(f"involves {', '.join(sorted(shared_deps))}")

    if current.region and incident.region == current.region:
        score += 0.05
        reasons.append(f"same region ({incident.region})")
    elif region_differs(current, incident):
        reasons.append(f"different region ({incident.region})")

    severity_gap = abs(
        _SEVERITY_RANK.get(current.severity.value, 3)
        - _SEVERITY_RANK.get(incident.severity.value, 3)
    )
    if severity_gap == 0:
        score += 0.05
        reasons.append(f"same severity ({incident.severity.value})")
    elif severity_gap == 1:
        score += 0.02

    overlap = _symptom_overlap(current, incident)
    if overlap > 0:
        score += 0.15 * overlap
        reasons.append(f"symptom overlap {overlap:.0%}")

    return MetadataMatch(score=min(1.0, score), reasons=tuple(reasons))


GLOBAL_REGION = "global"


def region_differs(current: CurrentIncident, incident: Incident) -> bool:
    """A known, different region. Global incidents and unknown regions never differ."""
    return bool(
        current.region
        and incident.region
        and incident.region != current.region
        and GLOBAL_REGION not in (incident.region, current.region)
    )


def service_graph_score(
    current: CurrentIncident, incident: Incident, graph: ServiceGraph
) -> tuple[float, str | None]:
    """How closely the two services sit in the dependency map."""
    if incident.service == current.service:
        return 1.0, None

    if incident.service in graph.deps_of(current.service):
        return 0.8, f"{incident.service} is a dependency of {current.service}"

    if current.service in graph.deps_of(incident.service):
        return 0.6, f"{current.service} is a dependency of {incident.service}"

    if set(current.suspected_dependencies) & {
        incident.service,
        *incident.related_services,
    }:
        return 0.5, f"shares a suspected dependency with {incident.service}"

    shared = graph.deps_of(current.service) & graph.deps_of(incident.service)
    if shared:
        return 0.4, f"both depend on {', '.join(sorted(shared))}"

    return 0.0, None


def trigger_coverage(current: CurrentIncident, runbook: Runbook) -> float:
    """Fraction of a runbook's trigger vocabulary present in the incident.

    A trigger is a stated condition in operational vocabulary, so exact token
    agreement is high-precision where the embedding is merely warm: every
    runbook about Redis reads alike to a dense vector, but only one of them
    names `evicted_keys`.
    """
    trigger_tokens = _tokens(f"{runbook.trigger} {runbook.title}")
    if not trigger_tokens:
        return 0.0
    return len(trigger_tokens & incident_tokens(current)) / len(trigger_tokens)


def incident_tokens(current: CurrentIncident) -> set[str]:
    return _tokens(
        " ".join(
            [
                current.title,
                current.description,
                *current.symptoms,
                *current.suspected_dependencies,
                *current.tech_context,
                *current.notes,
                *(event.description for event in current.events),
                current.log_excerpt or "",
            ]
        )
    )


def implicated_components(incident: Incident) -> frozenset[str]:
    """What the failure ran through, beyond the service it was filed against."""
    return frozenset(
        {*incident.related_services, *(a.component for a in incident.actions)}
        - {incident.service}
    )


@dataclass(frozen=True)
class ShapeMatch:
    shared: bool
    components: tuple[str, ...]
    timeline_similarity: float | None

    def describe(self) -> str:
        if not self.components:
            return "no shared dependency"
        parts = [f"fails through {', '.join(self.components)}"]
        if self.timeline_similarity is not None:
            parts.append(f"timeline shape {self.timeline_similarity:.2f}")
        return "; ".join(parts)


def failure_shape(left: Incident, right: Incident, *, min_similarity: float) -> ShapeMatch:
    """Do two incidents share a failure shape, not merely a service?

    Two payment-api incidents, one through Redis and one through Postgres, are
    different failures that happen to be filed in the same place. So the overlap
    has to involve an implicated dependency on at least one side, and when both
    have timelines, the order of events has to agree as well.
    """
    left_implicated = implicated_components(left)
    right_implicated = implicated_components(right)
    if left_implicated or right_implicated:
        shared = (left_implicated & ({right.service} | right_implicated)) | (
            right_implicated & ({left.service} | left_implicated)
        )
    else:
        shared = {left.service} & {right.service}

    similarity: float | None = None
    if left.events and right.events:
        similarity = align(left.events, right.events).sequence_score

    return ShapeMatch(
        shared=bool(shared) and (similarity is None or similarity >= min_similarity),
        components=tuple(sorted(shared)),
        timeline_similarity=similarity,
    )


def recency_score(incident: Incident, *, now: datetime, half_life_days: float) -> float:
    """Halves every `half_life_days`, so a stale near-duplicate cannot win on text."""
    age_days = max(0.0, (now - incident.started_at).total_seconds() / 86400)
    return 0.5 ** (age_days / half_life_days)


def age_days(incident: Incident, *, now: datetime) -> int:
    return max(0, (now - incident.started_at).days)


def is_stale(incident: Incident, *, now: datetime, stale_after_days: int) -> bool:
    return age_days(incident, now=now) > stale_after_days


def _symptom_overlap(current: CurrentIncident, incident: Incident) -> float:
    left = _tokens(
        " ".join([*current.symptoms, current.title, *current.tech_context])
    )
    right = _tokens(
        " ".join([*incident.symptoms, incident.title, *incident.tech_context])
    )
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN.findall(text.lower())
        if token not in _STOPWORDS and not token.isdigit()
    }
