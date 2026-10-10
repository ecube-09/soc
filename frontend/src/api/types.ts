export type ResponseMode = "investigation" | "escalation";
export type RootCauseStatus = "undetermined" | "hypothesis";
export type SourceType = "incident" | "postmortem" | "runbook" | "log_excerpt";

export type EventType =
  | "deploy"
  | "error_spike"
  | "latency_spike"
  | "dependency_timeout"
  | "alert"
  | "mitigation";

export const EVENT_TYPES: EventType[] = [
  "deploy",
  "error_spike",
  "latency_spike",
  "dependency_timeout",
  "alert",
  "mitigation",
];

export const ENVIRONMENTS = ["production", "staging", "development"] as const;
export const SEVERITIES = ["SEV-1", "SEV-2", "SEV-3", "SEV-4"] as const;

export interface TimelineEventInput {
  at: string;
  type: EventType;
  service: string;
  description: string;
}

export interface CurrentIncident {
  title: string;
  description: string;
  service: string;
  environment: string;
  severity: string;
  region: string | null;
  started_at: string | null;
  symptoms: string[];
  suspected_dependencies: string[];
  tech_context: string[];
  events: TimelineEventInput[];
  log_excerpt: string | null;
  notes?: string[];
  findings?: Finding[];
}

export interface Finding {
  target: string;
  component: string;
  favours_incident_id: string;
  observation: string;
  author?: string | null;
}

export interface NormalizedEvent {
  type: string;
  offset_minutes: number;
  service: string;
  description: string;
}

export interface MatchedPair {
  type: string;
  current_offset: number;
  historical_offset: number;
  drift_minutes: number;
}

export interface TimelineAlignment {
  sequence_score: number;
  interval_score: number;
  anchor: string;
  current: NormalizedEvent[];
  historical: NormalizedEvent[];
  matched: MatchedPair[];
}

export interface ScoreComponents {
  semantic: number;
  metadata: number;
  timeline_sequence: number;
  timeline_interval: number;
  recency: number;
  service_graph: number;
}

export interface Candidate {
  incident_id: string;
  title: string;
  summary: string;
  service: string;
  environment: string;
  severity: string;
  region: string;
  started_at: string;
  duration_minutes: number | null;
  age_days: number;
  root_cause: string;
  resolution: string;
  symptoms: string[];
  tech_context: string[];
  actions: string[];
  score: number;
  components: ScoreComponents;
  metadata_reasons: string[];
  graph_reason: string | null;
  flags: string[];
  timeline: TimelineAlignment;
  timeline_summary: string;
  postmortem_id: string | null;
  log_ids: string[];
  citation_ids: string[];
  supports_hypothesis: boolean;
  support_reason: string | null;
}

export interface RunbookMatch {
  runbook_id: string;
  title: string;
  trigger: string;
  service: string | null;
  steps: string[];
  escalate_to: string | null;
  version: string;
  updated_at: string;
  score: number;
  semantic: number;
  reason: string;
}

export interface ConflictSide {
  incident_id: string;
  title: string;
  action: string;
  direction: string;
  root_cause: string;
  age_days: number;
}

export interface Conflict {
  target: string;
  component: string;
  summary: string;
  distinguishing_question: string;
  more_recent_incident_id: string;
  sides: ConflictSide[];
  resolution: ConflictResolution | null;
}

export interface ConflictResolution {
  target: string;
  component: string;
  favours_incident_id: string;
  rejected_incident_id: string;
  observation: string;
  author: string | null;
}

export interface Support {
  incident_id: string;
  reason: string;
  stale: boolean;
}

export interface ExcludedCase {
  incident_id: string;
  title: string;
  reason: string;
  filter: "environment" | "region";
}

export interface Evidence {
  current_incident: CurrentIncident;
  mode: ResponseMode;
  confidence: number;
  gate_reason: string;
  escalate_to: string | null;
  candidates: Candidate[];
  runbooks: RunbookMatch[];
  conflicts: Conflict[];
  excluded: ExcludedCase[];
  support: Support[];
  unknowns: string[];
  notes: string[];
  citation_ids: string[];
}

export interface Step {
  action: string;
  rationale: string;
  citations: string[];
  would_falsify: string;
  support: number | null;
}

export interface EscalationReport {
  incident_title: string;
  severity: string;
  service: string;
  environment: string;
  observed_symptoms: string[];
  timeline: string[];
  historical_correlation: string[];
  evidence_gathered: string[];
  conflicting_guidance: string[];
  investigations_considered: Step[];
  unresolved: string[];
  requested_team: string;
  recommended_next_investigation: string[];
  citations: string[];
}

export interface RevisionSummary {
  revision: number | null;
  mode: ResponseMode;
  confidence: number;
  root_cause_status: RootCauseStatus;
  top_incident_id: string | null;
  created_at: string | null;
  author: string | null;
  reference_time: string | null;
}

export interface InvestigationResponse {
  case_id: string;
  revision: number;
  author: string | null;
  /** Wall clock when this revision ran. */
  created_at: string;
  /** The incident's own time; recency is measured against it. */
  reference_time: string | null;
  previous: RevisionSummary | null;
  changes: string[];
  evidence_counts: Record<string, number>;
  mode: ResponseMode;
  root_cause_status: RootCauseStatus;
  confidence: number;
  gate_reason: string;
  headline: string;
  root_cause_hypothesis: string | null;
  hypothesis_basis: string[];
  steps: Step[];
  cautions: string[];
  unresolved: string[];
  citations: string[];
  validation_notes: string[];
  escalation_report: EscalationReport | null;
  evidence: Evidence;
  rendered: string;
  rendered_markdown: string;
}

export type EvidenceKind = "note" | "log_excerpt" | "finding";

export interface Artifact {
  artifact_id: string;
  case_id: string;
  kind: string;
  created_at: string;
  author: string | null;
  payload: Record<string, unknown>;
}

export interface CaseSummary {
  case_id: string;
  title: string;
  created_at: string;
  created_by: string | null;
  incident_started_at: string | null;
  latest: RevisionSummary | null;
}

export interface CaseDetail extends Omit<CaseSummary, "latest"> {
  artifacts: Artifact[];
  revisions: RevisionSummary[];
}

export interface ProposedEvent {
  at: string;
  type: EventType;
  service: string;
  description: string;
  source_line: string;
  rule: string;
}

export interface TimelineExtraction {
  events: ProposedEvent[];
  lines_read: number;
  lines_without_time: number;
  lines_unclassified: number;
}

export interface Service {
  name: string;
  team: string;
  tier: string;
  description: string;
  dependencies: string[];
}

export interface Example {
  case_id: string;
  note: string;
  incident: CurrentIncident;
}

export interface Source {
  source_id: string;
  source_type: SourceType;
  title: string;
  body: string;
  metadata: Record<string, string | null>;
}

export interface CorpusStats {
  counts: Record<string, number>;
  indexed_chunks: number;
  supported_artifact_kinds: string[];
  reserved_artifact_kinds: string[];
  origins: Record<string, number>;
  region_policy: "prefer" | "require";
  eval_origin: string;
}
