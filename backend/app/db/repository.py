import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from app.domain import (
    ActionDirection,
    Document,
    Environment,
    EventType,
    Incident,
    LogExcerpt,
    Postmortem,
    ResolutionAction,
    Runbook,
    Service,
    Severity,
    SourceType,
    TimelineEvent,
)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Repository(Protocol):
    """Structured knowledge access. SQLite now; the callers never assume it."""

    def services(self) -> list[Service]: ...
    def incidents(self) -> list[Incident]: ...
    def incident(self, incident_id: str) -> Incident | None: ...
    def runbooks(self) -> list[Runbook]: ...
    def runbook(self, runbook_id: str) -> Runbook | None: ...
    def postmortems(self) -> list[Postmortem]: ...
    def postmortem_for(self, incident_id: str) -> Postmortem | None: ...
    def log_excerpts_for(self, incident_id: str) -> list[LogExcerpt]: ...
    def documents(self) -> list[Document]: ...
    def documents_by_ids(self, ids: Sequence[str]) -> list[Document]: ...


def _dumps(values: Iterable[str]) -> str:
    return json.dumps(list(values))


def _loads(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(json.loads(raw))


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_dt(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw) if raw else None


class SqliteRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self._conn.close()

    def init_schema(self) -> None:
        self._conn.executescript(SCHEMA_PATH.read_text())
        self._conn.commit()

    def replace_corpus(
        self,
        *,
        services: Sequence[Service],
        incidents: Sequence[Incident],
        runbooks: Sequence[Runbook],
        postmortems: Sequence[Postmortem],
        log_excerpts: Sequence[LogExcerpt],
    ) -> None:
        """Seed is idempotent: the corpus is authored, not accumulated."""
        with self._conn:
            for table in (
                "log_excerpts",
                "postmortems",
                "resolution_actions",
                "events",
                "incidents",
                "runbooks",
                "service_dependencies",
                "services",
            ):
                self._conn.execute(f"DELETE FROM {table}")

            self._conn.executemany(
                "INSERT INTO services (name, team, tier, description) VALUES (?, ?, ?, ?)",
                [(s.name, s.team, s.tier, s.description) for s in services],
            )
            self._conn.executemany(
                "INSERT INTO service_dependencies (service, depends_on) VALUES (?, ?)",
                [(s.name, dep) for s in services for dep in s.dependencies],
            )

            self._conn.executemany(
                """
                INSERT INTO incidents (
                    incident_id, title, summary, severity, service, environment, region,
                    started_at, resolved_at, root_cause, resolution,
                    symptoms, related_services, tech_context, origin
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        i.incident_id,
                        i.title,
                        i.summary,
                        i.severity.value,
                        i.service,
                        i.environment.value,
                        i.region,
                        _iso(i.started_at),
                        _iso(i.resolved_at) if i.resolved_at else None,
                        i.root_cause,
                        i.resolution,
                        _dumps(i.symptoms),
                        _dumps(i.related_services),
                        _dumps(i.tech_context),
                        i.origin,
                    )
                    for i in incidents
                ],
            )
            self._conn.executemany(
                """
                INSERT INTO events (incident_id, at, type, service, description)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (i.incident_id, _iso(e.at), e.type.value, e.service, e.description)
                    for i in incidents
                    for e in i.events
                ],
            )
            self._conn.executemany(
                """
                INSERT INTO resolution_actions (
                    incident_id, component, target, direction, from_value, to_value, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        i.incident_id,
                        a.component,
                        a.target,
                        a.direction.value,
                        a.from_value,
                        a.to_value,
                        a.note,
                    )
                    for i in incidents
                    for a in i.actions
                ],
            )

            self._conn.executemany(
                """
                INSERT INTO runbooks (
                    runbook_id, title, trigger, service, steps,
                    escalate_to, version, updated_at, origin
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r.runbook_id,
                        r.title,
                        r.trigger,
                        r.service,
                        _dumps(r.steps),
                        r.escalate_to,
                        r.version,
                        _iso(r.updated_at),
                        r.origin,
                    )
                    for r in runbooks
                ],
            )
            self._conn.executemany(
                """
                INSERT INTO postmortems (
                    postmortem_id, incident_id, root_cause, impact, resolution,
                    lessons_learned, written_at, origin
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        p.postmortem_id,
                        p.incident_id,
                        p.root_cause,
                        p.impact,
                        p.resolution,
                        _dumps(p.lessons_learned),
                        _iso(p.written_at),
                        p.origin,
                    )
                    for p in postmortems
                ],
            )
            self._conn.executemany(
                """
                INSERT INTO log_excerpts (
                    log_id, incident_id, service, captured_at, lines, origin
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        log.log_id,
                        log.incident_id,
                        log.service,
                        _iso(log.captured_at),
                        _dumps(log.lines),
                        log.origin,
                    )
                    for log in log_excerpts
                ],
            )

    def replace_documents(self, documents: Sequence[Document]) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM documents")
            self._conn.executemany(
                """
                INSERT INTO documents (
                    document_id, source_type, source_id, title, section,
                    text, service, environment, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        d.document_id,
                        d.source_type.value,
                        d.source_id,
                        d.title,
                        d.section,
                        d.text,
                        d.service,
                        d.environment.value if d.environment else None,
                        _iso(d.updated_at),
                    )
                    for d in documents
                ],
            )

    def counts(self) -> dict[str, int]:
        tables = (
            "services",
            "incidents",
            "events",
            "resolution_actions",
            "runbooks",
            "postmortems",
            "log_excerpts",
            "documents",
        )
        return {
            table: self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in tables
        }

    def services(self) -> list[Service]:
        deps: dict[str, list[str]] = {}
        for row in self._conn.execute(
            "SELECT service, depends_on FROM service_dependencies ORDER BY depends_on"
        ):
            deps.setdefault(row["service"], []).append(row["depends_on"])

        return [
            Service(
                name=row["name"],
                team=row["team"],
                tier=row["tier"],
                description=row["description"],
                dependencies=tuple(deps.get(row["name"], ())),
            )
            for row in self._conn.execute("SELECT * FROM services ORDER BY name")
        ]

    def incidents(self) -> list[Incident]:
        events = self._events_by_incident()
        actions = self._actions_by_incident()
        return [
            self._incident_from_row(row, events, actions)
            for row in self._conn.execute(
                "SELECT * FROM incidents ORDER BY started_at DESC"
            )
        ]

    def incident(self, incident_id: str) -> Incident | None:
        row = self._conn.execute(
            "SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)
        ).fetchone()
        if row is None:
            return None
        return self._incident_from_row(
            row,
            self._events_by_incident(incident_id),
            self._actions_by_incident(incident_id),
        )

    def runbooks(self) -> list[Runbook]:
        return [
            self._runbook_from_row(row)
            for row in self._conn.execute("SELECT * FROM runbooks ORDER BY runbook_id")
        ]

    def runbook(self, runbook_id: str) -> Runbook | None:
        row = self._conn.execute(
            "SELECT * FROM runbooks WHERE runbook_id = ?", (runbook_id,)
        ).fetchone()
        return self._runbook_from_row(row) if row else None

    def postmortems(self) -> list[Postmortem]:
        return [
            self._postmortem_from_row(row)
            for row in self._conn.execute(
                "SELECT * FROM postmortems ORDER BY postmortem_id"
            )
        ]

    def postmortem_for(self, incident_id: str) -> Postmortem | None:
        row = self._conn.execute(
            "SELECT * FROM postmortems WHERE incident_id = ?", (incident_id,)
        ).fetchone()
        return self._postmortem_from_row(row) if row else None

    def postmortem(self, postmortem_id: str) -> Postmortem | None:
        row = self._conn.execute(
            "SELECT * FROM postmortems WHERE postmortem_id = ?", (postmortem_id,)
        ).fetchone()
        return self._postmortem_from_row(row) if row else None

    def log_excerpts_for(self, incident_id: str) -> list[LogExcerpt]:
        return [
            self._log_from_row(row)
            for row in self._conn.execute(
                "SELECT * FROM log_excerpts WHERE incident_id = ? ORDER BY log_id",
                (incident_id,),
            )
        ]

    def log_excerpt(self, log_id: str) -> LogExcerpt | None:
        row = self._conn.execute(
            "SELECT * FROM log_excerpts WHERE log_id = ?", (log_id,)
        ).fetchone()
        return self._log_from_row(row) if row else None

    def documents(self) -> list[Document]:
        return [
            self._document_from_row(row)
            for row in self._conn.execute("SELECT * FROM documents ORDER BY document_id")
        ]

    def documents_by_ids(self, ids: Sequence[str]) -> list[Document]:
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(
            f"SELECT * FROM documents WHERE document_id IN ({placeholders})", tuple(ids)
        )
        by_id = {row["document_id"]: self._document_from_row(row) for row in rows}
        return [by_id[i] for i in ids if i in by_id]

    def create_case(self, case_id: str, title: str, created_at: datetime) -> None:
        """Create the case if it is new, otherwise leave it alone.

        Not INSERT OR REPLACE: that deletes the row first, and artifacts cascade
        on delete, so re-registering a case would silently drop its history.
        """
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO cases (case_id, title, created_at) VALUES (?, ?, ?)
                ON CONFLICT(case_id) DO NOTHING
                """,
                (case_id, title, _iso(created_at)),
            )

    def add_artifact(
        self,
        *,
        artifact_id: str,
        case_id: str,
        kind: str,
        payload: dict[str, Any],
        created_at: datetime,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO artifacts (artifact_id, case_id, kind, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    case_id,
                    kind,
                    json.dumps(payload, default=str),
                    _iso(created_at),
                ),
            )

    def case_artifacts(self, case_id: str) -> list[dict[str, Any]]:
        return [
            {
                "artifact_id": row["artifact_id"],
                "kind": row["kind"],
                "created_at": row["created_at"],
                "payload": json.loads(row["payload"]),
            }
            for row in self._conn.execute(
                "SELECT * FROM artifacts WHERE case_id = ? ORDER BY created_at",
                (case_id,),
            )
        ]

    def _events_by_incident(
        self, incident_id: str | None = None
    ) -> dict[str, list[TimelineEvent]]:
        sql = "SELECT * FROM events"
        params: tuple[str, ...] = ()
        if incident_id:
            sql += " WHERE incident_id = ?"
            params = (incident_id,)
        sql += " ORDER BY at, event_id"

        out: dict[str, list[TimelineEvent]] = {}
        for row in self._conn.execute(sql, params):
            out.setdefault(row["incident_id"], []).append(
                TimelineEvent(
                    at=datetime.fromisoformat(row["at"]),
                    type=EventType(row["type"]),
                    service=row["service"],
                    description=row["description"],
                )
            )
        return out

    def _actions_by_incident(
        self, incident_id: str | None = None
    ) -> dict[str, list[ResolutionAction]]:
        sql = "SELECT * FROM resolution_actions"
        params: tuple[str, ...] = ()
        if incident_id:
            sql += " WHERE incident_id = ?"
            params = (incident_id,)
        sql += " ORDER BY action_id"

        out: dict[str, list[ResolutionAction]] = {}
        for row in self._conn.execute(sql, params):
            out.setdefault(row["incident_id"], []).append(
                ResolutionAction(
                    component=row["component"],
                    target=row["target"],
                    direction=ActionDirection(row["direction"]),
                    from_value=row["from_value"],
                    to_value=row["to_value"],
                    note=row["note"],
                )
            )
        return out

    @staticmethod
    def _incident_from_row(
        row: sqlite3.Row,
        events: dict[str, list[TimelineEvent]],
        actions: dict[str, list[ResolutionAction]],
    ) -> Incident:
        incident_id = row["incident_id"]
        return Incident(
            incident_id=incident_id,
            title=row["title"],
            summary=row["summary"],
            severity=Severity(row["severity"]),
            service=row["service"],
            environment=Environment(row["environment"]),
            region=row["region"],
            started_at=datetime.fromisoformat(row["started_at"]),
            resolved_at=_parse_dt(row["resolved_at"]),
            root_cause=row["root_cause"],
            resolution=row["resolution"],
            symptoms=_loads(row["symptoms"]),
            related_services=_loads(row["related_services"]),
            tech_context=_loads(row["tech_context"]),
            events=tuple(events.get(incident_id, ())),
            actions=tuple(actions.get(incident_id, ())),
            origin=row["origin"],
        )

    @staticmethod
    def _runbook_from_row(row: sqlite3.Row) -> Runbook:
        return Runbook(
            runbook_id=row["runbook_id"],
            title=row["title"],
            trigger=row["trigger"],
            service=row["service"],
            steps=_loads(row["steps"]),
            escalate_to=row["escalate_to"],
            version=row["version"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
            origin=row["origin"],
        )

    @staticmethod
    def _postmortem_from_row(row: sqlite3.Row) -> Postmortem:
        return Postmortem(
            postmortem_id=row["postmortem_id"],
            incident_id=row["incident_id"],
            root_cause=row["root_cause"],
            impact=row["impact"],
            resolution=row["resolution"],
            lessons_learned=_loads(row["lessons_learned"]),
            written_at=datetime.fromisoformat(row["written_at"]),
            origin=row["origin"],
        )

    @staticmethod
    def _log_from_row(row: sqlite3.Row) -> LogExcerpt:
        return LogExcerpt(
            log_id=row["log_id"],
            incident_id=row["incident_id"],
            service=row["service"],
            captured_at=datetime.fromisoformat(row["captured_at"]),
            lines=_loads(row["lines"]),
            origin=row["origin"],
        )

    @staticmethod
    def _document_from_row(row: sqlite3.Row) -> Document:
        return Document(
            document_id=row["document_id"],
            source_type=SourceType(row["source_type"]),
            source_id=row["source_id"],
            title=row["title"],
            section=row["section"],
            text=row["text"],
            service=row["service"],
            environment=Environment(row["environment"]) if row["environment"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
