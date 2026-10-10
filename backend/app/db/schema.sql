PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS services (
    name        TEXT PRIMARY KEY,
    team        TEXT NOT NULL,
    tier        TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS service_dependencies (
    service    TEXT NOT NULL REFERENCES services(name) ON DELETE CASCADE,
    depends_on TEXT NOT NULL,
    PRIMARY KEY (service, depends_on)
);

CREATE TABLE IF NOT EXISTS incidents (
    incident_id      TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    summary          TEXT NOT NULL,
    severity         TEXT NOT NULL,
    service          TEXT NOT NULL,
    environment      TEXT NOT NULL,
    region           TEXT NOT NULL,
    started_at       TEXT NOT NULL,
    resolved_at      TEXT,
    root_cause       TEXT NOT NULL,
    resolution       TEXT NOT NULL,
    symptoms         TEXT NOT NULL DEFAULT '[]',
    related_services TEXT NOT NULL DEFAULT '[]',
    tech_context     TEXT NOT NULL DEFAULT '[]',
    origin           TEXT NOT NULL DEFAULT 'synthetic'
);

CREATE INDEX IF NOT EXISTS idx_incidents_service ON incidents(service, environment);
CREATE INDEX IF NOT EXISTS idx_incidents_started ON incidents(started_at DESC);

CREATE TABLE IF NOT EXISTS events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    at          TEXT NOT NULL,
    type        TEXT NOT NULL,
    service     TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_incident ON events(incident_id, at);

-- Structured remediation. Conflicting-fix detection reads this, not prose.
CREATE TABLE IF NOT EXISTS resolution_actions (
    action_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    component   TEXT NOT NULL,
    target      TEXT NOT NULL,
    direction   TEXT NOT NULL,
    from_value  TEXT,
    to_value    TEXT,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_actions_target ON resolution_actions(target);

CREATE TABLE IF NOT EXISTS runbooks (
    runbook_id  TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    trigger     TEXT NOT NULL,
    service     TEXT,
    steps       TEXT NOT NULL DEFAULT '[]',
    escalate_to TEXT,
    version     TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    origin      TEXT NOT NULL DEFAULT 'synthetic'
);

CREATE TABLE IF NOT EXISTS postmortems (
    postmortem_id   TEXT PRIMARY KEY,
    incident_id     TEXT NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    root_cause      TEXT NOT NULL,
    impact          TEXT NOT NULL,
    resolution      TEXT NOT NULL,
    lessons_learned TEXT NOT NULL DEFAULT '[]',
    written_at      TEXT NOT NULL,
    origin          TEXT NOT NULL DEFAULT 'synthetic'
);

CREATE TABLE IF NOT EXISTS log_excerpts (
    log_id      TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    service     TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    lines       TEXT NOT NULL DEFAULT '[]',
    origin      TEXT NOT NULL DEFAULT 'synthetic'
);

-- Retrievable chunks. Every row carries the id a citation must point at.
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    title       TEXT NOT NULL,
    section     TEXT,
    text        TEXT NOT NULL,
    service     TEXT,
    environment TEXT,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_type, source_id);

-- A case is the unit of work. The current incident is its first artifact;
-- later uploads (logs, notes, findings) attach to the same case.
-- created_at is the wall clock when someone opened the case. incident_started_at
-- is the incident's own time, which is what recency is measured against.
CREATE TABLE IF NOT EXISTS cases (
    case_id             TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    created_by          TEXT,
    incident_started_at TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    case_id     TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    author      TEXT
);

CREATE INDEX IF NOT EXISTS idx_artifacts_case ON artifacts(case_id, created_at);

-- Every run of the pipeline on a case, kept whole: the evidence pack, the
-- hypothesis or escalation report, and the validation notes. A re-run after new
-- evidence is a new revision, so the case shows how the answer moved.
CREATE TABLE IF NOT EXISTS investigations (
    investigation_id  TEXT PRIMARY KEY,
    case_id           TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    revision          INTEGER NOT NULL,
    created_at        TEXT NOT NULL,
    reference_time    TEXT,
    author            TEXT,
    mode              TEXT NOT NULL,
    confidence        REAL NOT NULL,
    root_cause_status TEXT NOT NULL,
    top_incident_id   TEXT,
    result            TEXT NOT NULL,
    UNIQUE (case_id, revision)
);

CREATE INDEX IF NOT EXISTS idx_investigations_case ON investigations(case_id, revision);
