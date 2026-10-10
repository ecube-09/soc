import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.domain import (
    ActionDirection,
    CurrentIncident,
    Environment,
    EventType,
    Finding,
    Incident,
    LogExcerpt,
    Postmortem,
    ResolutionAction,
    Runbook,
    Service,
    Severity,
    TimelineEvent,
)

DATA_DIR = Path(__file__).with_name("data")


@dataclass(frozen=True)
class Corpus:
    services: tuple[Service, ...]
    incidents: tuple[Incident, ...]
    runbooks: tuple[Runbook, ...]
    postmortems: tuple[Postmortem, ...]
    log_excerpts: tuple[LogExcerpt, ...]


@dataclass(frozen=True)
class ExpectedOutcome:
    """Labels for the eval set: what a correct answer must contain."""

    mode: str
    incident_ids: tuple[str, ...]
    runbook_ids: tuple[str, ...]
    stale_ids: tuple[str, ...]
    excluded_ids: tuple[str, ...]
    conflict_targets: tuple[str, ...]


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    note: str
    incident: CurrentIncident
    expected: ExpectedOutcome


def load_corpus(data_dir: Path = DATA_DIR) -> Corpus:
    return Corpus(
        services=tuple(_service(r) for r in _read(data_dir / "services.json")),
        incidents=tuple(_incident(r) for r in _read(data_dir / "incidents.json")),
        runbooks=tuple(_runbook(r) for r in _read(data_dir / "runbooks.json")),
        postmortems=tuple(_postmortem(r) for r in _read(data_dir / "postmortems.json")),
        log_excerpts=tuple(_log(r) for r in _read(data_dir / "log_excerpts.json")),
    )


def load_eval_cases(data_dir: Path = DATA_DIR) -> tuple[EvalCase, ...]:
    """Test inputs, not corpus knowledge: these never reach the vector store."""
    return tuple(_eval_case(r) for r in _read(data_dir / "eval_cases.json"))


def parse_current_incident(payload: dict[str, Any]) -> CurrentIncident:
    return CurrentIncident(
        title=payload["title"],
        description=payload["description"],
        service=payload["service"],
        environment=Environment(payload["environment"]),
        severity=Severity(payload["severity"]),
        region=payload.get("region"),
        symptoms=tuple(payload.get("symptoms", ())),
        suspected_dependencies=tuple(payload.get("suspected_dependencies", ())),
        events=tuple(_event(e) for e in payload.get("events", ())),
        log_excerpt=payload.get("log_excerpt"),
        started_at=_dt(payload.get("started_at")),
        tech_context=tuple(payload.get("tech_context", ())),
        notes=tuple(payload.get("notes", ())),
        findings=tuple(parse_finding(f) for f in payload.get("findings", ())),
    )


def parse_finding(row: dict[str, Any]) -> Finding:
    observation = str(row.get("observation") or "").strip()
    if not observation:
        raise ValueError("A finding needs the observation it is based on.")
    return Finding(
        target=row["target"],
        component=row["component"],
        favours_incident_id=row["favours_incident_id"],
        observation=observation,
        author=row.get("author"),
    )


def _read(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


def _dt(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw) if raw else None


def _service(row: dict[str, Any]) -> Service:
    return Service(
        name=row["name"],
        team=row["team"],
        tier=row["tier"],
        description=row["description"],
        dependencies=tuple(row.get("dependencies", ())),
    )


def _event(row: dict[str, Any]) -> TimelineEvent:
    return TimelineEvent(
        at=datetime.fromisoformat(row["at"]),
        type=EventType(row["type"]),
        service=row["service"],
        description=row["description"],
    )


def _action(row: dict[str, Any]) -> ResolutionAction:
    return ResolutionAction(
        component=row["component"],
        target=row["target"],
        direction=ActionDirection(row["direction"]),
        from_value=row.get("from_value"),
        to_value=row.get("to_value"),
        note=row.get("note"),
    )


def _incident(row: dict[str, Any]) -> Incident:
    return Incident(
        incident_id=row["incident_id"],
        title=row["title"],
        summary=row["summary"],
        severity=Severity(row["severity"]),
        service=row["service"],
        environment=Environment(row["environment"]),
        region=row["region"],
        started_at=datetime.fromisoformat(row["started_at"]),
        resolved_at=_dt(row.get("resolved_at")),
        root_cause=row["root_cause"],
        resolution=row["resolution"],
        symptoms=tuple(row.get("symptoms", ())),
        related_services=tuple(row.get("related_services", ())),
        tech_context=tuple(row.get("tech_context", ())),
        events=tuple(_event(e) for e in row.get("events", ())),
        actions=tuple(_action(a) for a in row.get("actions", ())),
        origin=row.get("origin", "synthetic"),
    )


def _runbook(row: dict[str, Any]) -> Runbook:
    return Runbook(
        runbook_id=row["runbook_id"],
        title=row["title"],
        trigger=row["trigger"],
        service=row.get("service"),
        steps=tuple(row.get("steps", ())),
        escalate_to=row.get("escalate_to"),
        version=row["version"],
        updated_at=datetime.fromisoformat(row["updated_at"]),
        origin=row.get("origin", "synthetic"),
    )


def _postmortem(row: dict[str, Any]) -> Postmortem:
    return Postmortem(
        postmortem_id=row["postmortem_id"],
        incident_id=row["incident_id"],
        root_cause=row["root_cause"],
        impact=row["impact"],
        resolution=row["resolution"],
        lessons_learned=tuple(row.get("lessons_learned", ())),
        written_at=datetime.fromisoformat(row["written_at"]),
        origin=row.get("origin", "synthetic"),
    )


def _log(row: dict[str, Any]) -> LogExcerpt:
    return LogExcerpt(
        log_id=row["log_id"],
        incident_id=row["incident_id"],
        service=row["service"],
        captured_at=datetime.fromisoformat(row["captured_at"]),
        lines=tuple(row.get("lines", ())),
        origin=row.get("origin", "synthetic"),
    )


def _eval_case(row: dict[str, Any]) -> EvalCase:
    expected = row["expected"]
    return EvalCase(
        case_id=row["case_id"],
        note=row["note"],
        incident=parse_current_incident(row["incident"]),
        expected=ExpectedOutcome(
            mode=expected["mode"],
            incident_ids=tuple(expected.get("incident_ids", ())),
            runbook_ids=tuple(expected.get("runbook_ids", ())),
            stale_ids=tuple(expected.get("stale_ids", ())),
            excluded_ids=tuple(expected.get("excluded_ids", ())),
            conflict_targets=tuple(expected.get("conflict_targets", ())),
        ),
    )
