"""Wire shapes for the workspace.

These mirror the `to_dict()` output of the domain results, so serialisation
stays in one place and the OpenAPI document stays honest about what the UI gets.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.domain import Environment, EventType, Severity


class TimelineEventIn(BaseModel):
    at: str
    type: EventType
    service: str
    description: str = ""


class FindingIn(BaseModel):
    """A measurement that settles a conflicting fix. Recorded by a person."""

    target: str
    component: str
    favours_incident_id: str
    observation: str = Field(min_length=1)
    author: str | None = None


class CurrentIncidentIn(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""
    service: str = Field(min_length=1)
    environment: Environment = Environment.PRODUCTION
    severity: Severity = Severity.SEV3
    region: str | None = None
    started_at: str | None = None
    symptoms: list[str] = Field(default_factory=list)
    suspected_dependencies: list[str] = Field(default_factory=list)
    tech_context: list[str] = Field(default_factory=list)
    events: list[TimelineEventIn] = Field(default_factory=list)
    log_excerpt: str | None = None
    notes: list[str] = Field(default_factory=list)
    findings: list[FindingIn] = Field(default_factory=list)


class InvestigationRequest(BaseModel):
    incident: CurrentIncidentIn
    case_id: str | None = Field(
        default=None,
        description="Attach to an existing case. A new one is created when omitted.",
    )
    author: str | None = Field(default=None, description="Who is investigating.")


class RerunRequest(BaseModel):
    author: str | None = None
    incident: CurrentIncidentIn | None = Field(
        default=None,
        description=(
            "An edited incident, for example with accepted timeline events. "
            "Omit to re-run the incident already on the case."
        ),
    )


class NormalizedEventOut(BaseModel):
    type: str
    offset_minutes: int
    service: str
    description: str


class MatchedPairOut(BaseModel):
    type: str
    current_offset: int
    historical_offset: int
    drift_minutes: int


class TimelineAlignmentOut(BaseModel):
    sequence_score: float
    interval_score: float
    anchor: str
    current: list[NormalizedEventOut]
    historical: list[NormalizedEventOut]
    matched: list[MatchedPairOut]


class ScoreComponentsOut(BaseModel):
    semantic: float
    metadata: float
    timeline_sequence: float
    timeline_interval: float
    recency: float
    service_graph: float


class CandidateOut(BaseModel):
    incident_id: str
    title: str
    summary: str
    service: str
    environment: str
    severity: str
    region: str
    started_at: str
    duration_minutes: int | None
    age_days: int
    root_cause: str
    resolution: str
    symptoms: list[str]
    tech_context: list[str]
    actions: list[str]
    score: float
    components: ScoreComponentsOut
    metadata_reasons: list[str]
    graph_reason: str | None
    flags: list[str]
    timeline: TimelineAlignmentOut
    timeline_summary: str
    postmortem_id: str | None
    log_ids: list[str]
    citation_ids: list[str]
    supports_hypothesis: bool = False
    support_reason: str | None = None


class RunbookOut(BaseModel):
    runbook_id: str
    title: str
    trigger: str
    service: str | None
    steps: list[str]
    escalate_to: str | None
    version: str
    updated_at: str
    score: float
    semantic: float
    reason: str


class ConflictSideOut(BaseModel):
    incident_id: str
    title: str
    action: str
    direction: str
    root_cause: str
    age_days: int


class ResolutionOut(BaseModel):
    target: str
    component: str
    favours_incident_id: str
    rejected_incident_id: str
    observation: str
    author: str | None


class ConflictOut(BaseModel):
    target: str
    component: str
    summary: str
    distinguishing_question: str
    more_recent_incident_id: str
    sides: list[ConflictSideOut]
    resolution: ResolutionOut | None = None


class ExcludedOut(BaseModel):
    incident_id: str
    title: str
    reason: str
    filter: Literal["environment", "region"] = "environment"


class SupportOut(BaseModel):
    incident_id: str
    reason: str
    stale: bool


class CurrentIncidentOut(BaseModel):
    title: str
    description: str
    service: str
    environment: str
    severity: str
    region: str | None
    started_at: str | None
    symptoms: list[str]
    suspected_dependencies: list[str]
    tech_context: list[str]
    events: list[TimelineEventIn]
    log_excerpt: str | None
    notes: list[str] = Field(default_factory=list)
    findings: list[FindingIn] = Field(default_factory=list)


class EvidenceOut(BaseModel):
    current_incident: CurrentIncidentOut
    mode: Literal["investigation", "escalation"]
    confidence: float
    gate_reason: str
    escalate_to: str | None
    candidates: list[CandidateOut]
    runbooks: list[RunbookOut]
    conflicts: list[ConflictOut]
    excluded: list[ExcludedOut]
    support: list[SupportOut] = Field(default_factory=list)
    unknowns: list[str]
    notes: list[str]
    citation_ids: list[str]


class StepOut(BaseModel):
    action: str
    rationale: str
    citations: list[str]
    would_falsify: str = ""
    support: float | None = None


class EscalationReportOut(BaseModel):
    incident_title: str
    severity: str
    service: str
    environment: str
    observed_symptoms: list[str]
    timeline: list[str]
    historical_correlation: list[str]
    evidence_gathered: list[str]
    conflicting_guidance: list[str] = Field(default_factory=list)
    investigations_considered: list[StepOut]
    unresolved: list[str]
    requested_team: str
    recommended_next_investigation: list[str]
    citations: list[str]


class RevisionSummaryOut(BaseModel):
    revision: int | None
    mode: Literal["investigation", "escalation"]
    confidence: float
    root_cause_status: Literal["undetermined", "hypothesis"]
    top_incident_id: str | None
    created_at: str | None
    author: str | None = None
    reference_time: str | None = None


class InvestigationResponse(BaseModel):
    case_id: str
    revision: int
    author: str | None
    created_at: str = Field(description="Wall clock when this revision ran.")
    reference_time: str | None = Field(
        description="The incident's own time; recency is measured against this."
    )
    previous: RevisionSummaryOut | None
    changes: list[str]
    evidence_counts: dict[str, int]
    mode: Literal["investigation", "escalation"]
    root_cause_status: Literal["undetermined", "hypothesis"]
    confidence: float
    gate_reason: str
    headline: str
    root_cause_hypothesis: str | None
    hypothesis_basis: list[str] = Field(default_factory=list)
    steps: list[StepOut]
    cautions: list[str]
    unresolved: list[str]
    citations: list[str]
    validation_notes: list[str]
    escalation_report: EscalationReportOut | None
    evidence: EvidenceOut
    rendered: str
    rendered_markdown: str


class ServiceOut(BaseModel):
    name: str
    team: str
    tier: str
    description: str
    dependencies: list[str]


class ExampleOut(BaseModel):
    case_id: str
    note: str
    incident: CurrentIncidentOut


class SourceOut(BaseModel):
    """What the citation panel shows when an id is clicked."""

    source_id: str
    source_type: Literal["incident", "postmortem", "runbook", "log_excerpt"]
    title: str
    body: str
    metadata: dict[str, str | None]


class ArtifactRequest(BaseModel):
    kind: str
    payload: dict = Field(default_factory=dict)
    author: str | None = None


class ArtifactOut(BaseModel):
    artifact_id: str
    case_id: str
    kind: str
    created_at: str
    author: str | None = None


class ArtifactDetailOut(ArtifactOut):
    payload: dict


class CaseSummaryOut(BaseModel):
    case_id: str
    title: str
    created_at: str
    created_by: str | None
    incident_started_at: str | None
    latest: RevisionSummaryOut | None = None


class CaseDetailOut(BaseModel):
    case_id: str
    title: str
    created_at: str
    created_by: str | None
    incident_started_at: str | None
    artifacts: list[ArtifactDetailOut]
    revisions: list[RevisionSummaryOut]


class TimelineExtractRequest(BaseModel):
    log_text: str = Field(min_length=1)
    service: str = Field(min_length=1, description="Default service for unattributed lines.")
    date: str | None = Field(
        default=None,
        description="ISO date for lines that carry only a clock time.",
    )


class ProposedEventOut(BaseModel):
    at: str
    type: EventType
    service: str
    description: str
    source_line: str
    rule: str


class TimelineExtractOut(BaseModel):
    events: list[ProposedEventOut]
    lines_read: int
    lines_without_time: int
    lines_unclassified: int


class CorpusStatsOut(BaseModel):
    counts: dict[str, int]
    indexed_chunks: int
    supported_artifact_kinds: list[str]
    reserved_artifact_kinds: list[str]
    origins: dict[str, int] = Field(default_factory=dict)
    region_policy: Literal["prefer", "require"] = "prefer"
    eval_origin: str = "synthetic"
