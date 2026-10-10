from dataclasses import dataclass, field
from datetime import datetime

from app.domain.enums import (
    ActionDirection,
    Environment,
    EventType,
    Severity,
    SourceType,
)


@dataclass(frozen=True)
class Service:
    name: str
    team: str
    tier: str
    description: str
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class TimelineEvent:
    at: datetime
    type: EventType
    service: str
    description: str


@dataclass(frozen=True)
class ResolutionAction:
    """Structured remediation, so opposite historical fixes can be detected."""

    component: str
    target: str
    direction: ActionDirection
    from_value: str | None = None
    to_value: str | None = None
    note: str | None = None

    def describe(self) -> str:
        change = (
            f" {self.from_value} -> {self.to_value}"
            if self.from_value and self.to_value
            else ""
        )
        return f"{self.direction.value} {self.target} on {self.component}{change}"


@dataclass(frozen=True)
class Incident:
    incident_id: str
    title: str
    summary: str
    severity: Severity
    service: str
    environment: Environment
    region: str
    started_at: datetime
    resolved_at: datetime | None
    root_cause: str
    resolution: str
    symptoms: tuple[str, ...] = ()
    related_services: tuple[str, ...] = ()
    tech_context: tuple[str, ...] = ()
    events: tuple[TimelineEvent, ...] = ()
    actions: tuple[ResolutionAction, ...] = ()
    origin: str = "synthetic"

    @property
    def duration_minutes(self) -> int | None:
        if self.resolved_at is None:
            return None
        return int((self.resolved_at - self.started_at).total_seconds() // 60)


@dataclass(frozen=True)
class Runbook:
    runbook_id: str
    title: str
    trigger: str
    service: str | None
    steps: tuple[str, ...]
    escalate_to: str | None
    version: str
    updated_at: datetime
    origin: str = "synthetic"


@dataclass(frozen=True)
class Postmortem:
    postmortem_id: str
    incident_id: str
    root_cause: str
    impact: str
    resolution: str
    lessons_learned: tuple[str, ...]
    written_at: datetime
    origin: str = "synthetic"


@dataclass(frozen=True)
class LogExcerpt:
    log_id: str
    incident_id: str
    service: str
    captured_at: datetime
    lines: tuple[str, ...]
    origin: str = "synthetic"


@dataclass(frozen=True)
class Document:
    """A retrievable chunk that always points back to a citable source id."""

    document_id: str
    source_type: SourceType
    source_id: str
    title: str
    text: str
    service: str | None
    environment: Environment | None
    updated_at: datetime
    section: str | None = None


@dataclass(frozen=True)
class Finding:
    """A measurement an engineer recorded to decide a conflicting fix.

    Supplied by a person, never inferred. The system does not pick a side of a
    conflict; a finding is how a human tells it which side the evidence is on.
    """

    target: str
    component: str
    favours_incident_id: str
    observation: str
    author: str | None = None


@dataclass(frozen=True)
class CurrentIncident:
    """The incident under investigation. Not part of the historical corpus."""

    title: str
    description: str
    service: str
    environment: Environment
    severity: Severity
    region: str | None = None
    symptoms: tuple[str, ...] = ()
    suspected_dependencies: tuple[str, ...] = ()
    events: tuple[TimelineEvent, ...] = ()
    log_excerpt: str | None = None
    started_at: datetime | None = None
    tech_context: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()

    @property
    def anchor_time(self) -> datetime | None:
        if self.started_at:
            return self.started_at
        return min((e.at for e in self.events), default=None)
