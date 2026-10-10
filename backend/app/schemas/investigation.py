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


class InvestigationRequest(BaseModel):
    incident: CurrentIncidentIn
    case_id: str | None = Field(
        default=None,
        description="Attach to an existing case. A new one is created when omitted.",
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


class ConflictOut(BaseModel):
    target: str
    component: str
    summary: str
    distinguishing_question: str
    more_recent_incident_id: str
    sides: list[ConflictSideOut]


class ExcludedOut(BaseModel):
    incident_id: str
    title: str
    reason: str


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
    unknowns: list[str]
    notes: list[str]
    citation_ids: list[str]


class StepOut(BaseModel):
    action: str
    rationale: str
    citations: list[str]


class EscalationReportOut(BaseModel):
    incident_title: str
    severity: str
    service: str
    environment: str
    observed_symptoms: list[str]
    timeline: list[str]
    historical_correlation: list[str]
    evidence_gathered: list[str]
    investigations_considered: list[StepOut]
    unresolved: list[str]
    requested_team: str
    recommended_next_investigation: list[str]
    citations: list[str]


class InvestigationResponse(BaseModel):
    case_id: str
    mode: Literal["investigation", "escalation"]
    root_cause_status: Literal["undetermined", "hypothesis"]
    confidence: float
    gate_reason: str
    headline: str
    root_cause_hypothesis: str | None
    steps: list[StepOut]
    cautions: list[str]
    unresolved: list[str]
    citations: list[str]
    validation_notes: list[str]
    escalation_report: EscalationReportOut | None
    evidence: EvidenceOut
    rendered: str


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


class ArtifactOut(BaseModel):
    artifact_id: str
    case_id: str
    kind: str
    created_at: str


class CorpusStatsOut(BaseModel):
    counts: dict[str, int]
    indexed_chunks: int
    supported_artifact_kinds: list[str]
    reserved_artifact_kinds: list[str]
