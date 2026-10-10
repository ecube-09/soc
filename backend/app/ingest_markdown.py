"""Import a Markdown postmortem into the same incident, event, and action tables.

The point is to show the system is not limited to hand-written JSON rows: a
public postmortem written up in this shape lands in the same structures the
ranking, timeline alignment, and conflict detection read.

Two rules keep outside text honest:

- every import carries an `origin` other than `synthetic`, and it is shown
  wherever the source is shown;
- the labelled eval only scores against `synthetic`, so an import can change
  what an engineer sees but never a reported eval number.

Format (front matter, then `##` sections; unknown sections are ignored):

    ---
    incident_id: EXT-0001
    title: Checkout errors after a cache cluster upgrade
    service: checkout-api
    environment: production
    severity: SEV-2
    region: us-east-1
    started_at: 2024-03-02T10:15
    resolved_at: 2024-03-02T11:40
    origin: public:https://example.org/postmortems/cache-upgrade
    related_services: redis-cache
    tech_context: Redis 7.0, Kubernetes 1.28
    ---
    ## Summary
    ## Symptoms        (bullets)
    ## Timeline        (bullets: "10:15 deploy checkout-api: text", or any log-like line)
    ## Root cause
    ## Resolution
    ## Actions         (bullets: "increase redis_maxmemory on redis-cache 4GB -> 8GB")
    ## Impact
    ## Lessons learned (bullets)
"""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.analysis import log_events
from app.db.repository import SqliteRepository
from app.domain import (
    ActionDirection,
    Environment,
    EventType,
    Incident,
    Postmortem,
    ResolutionAction,
    Severity,
    TimelineEvent,
)

SYNTHETIC = "synthetic"
REQUIRED = ("incident_id", "title", "service", "environment", "severity", "started_at")

_EXPLICIT_EVENT = re.compile(
    r"^(?P<at>(?:\d{4}-\d{2}-\d{2}[T ])?\d{1,2}:\d{2})\s+"
    rf"(?P<type>{'|'.join(t.value for t in EventType)})\s+"
    r"(?:(?P<service>[a-z0-9][a-z0-9-]*)\s*:\s*)?(?P<desc>.*)$"
)
_ACTION = re.compile(
    rf"^(?P<direction>{'|'.join(d.value for d in ActionDirection)})\s+"
    r"(?P<target>[A-Za-z0-9_.-]+)\s+on\s+(?P<component>[a-z0-9][a-z0-9-]*)"
    r"(?:\s+(?P<from>\S+)\s*->\s*(?P<to>\S+))?\s*$"
)


class MarkdownImportError(ValueError):
    pass


@dataclass(frozen=True)
class Imported:
    incident: Incident
    postmortem: Postmortem
    warnings: tuple[str, ...]


def parse(text: str, *, origin: str | None = None) -> Imported:
    front, sections = _split(text)
    warnings: list[str] = []

    missing = [key for key in REQUIRED if not front.get(key)]
    if missing:
        raise MarkdownImportError(f"Missing front matter: {', '.join(missing)}.")

    origin = origin or front.get("origin")
    if not origin:
        raise MarkdownImportError("An import needs an `origin` saying where the text came from.")
    if origin == SYNTHETIC:
        raise MarkdownImportError(
            "`synthetic` is reserved for the authored corpus. Label imported text "
            "with where it came from, e.g. public:<url>."
        )

    for name in ("root cause", "resolution"):
        if not sections.get(name):
            raise MarkdownImportError(f"Missing a `## {name.capitalize()}` section.")

    started_at = datetime.fromisoformat(front["started_at"])
    service = front["service"]
    events, event_warnings = _events(sections.get("timeline", ""), started_at, service)
    warnings.extend(event_warnings)
    actions, action_warnings = _actions(sections.get("actions", ""))
    warnings.extend(action_warnings)
    if not events:
        warnings.append("No timeline events parsed; timeline alignment will not use this case.")
    if not actions:
        warnings.append("No structured actions parsed; conflict detection will not see this case.")

    incident = Incident(
        incident_id=front["incident_id"],
        title=front["title"],
        summary=sections.get("summary", "").strip() or front["title"],
        severity=Severity(front["severity"]),
        service=service,
        environment=Environment(front["environment"]),
        region=front.get("region") or "unknown",
        started_at=started_at,
        resolved_at=datetime.fromisoformat(front["resolved_at"])
        if front.get("resolved_at")
        else None,
        root_cause=sections["root cause"].strip(),
        resolution=sections["resolution"].strip(),
        symptoms=tuple(_bullets(sections.get("symptoms", ""))),
        related_services=tuple(_csv(front.get("related_services", ""))),
        tech_context=tuple(_csv(front.get("tech_context", ""))),
        events=tuple(events),
        actions=tuple(actions),
        origin=origin,
    )
    postmortem = Postmortem(
        postmortem_id=f"PM-{incident.incident_id}",
        incident_id=incident.incident_id,
        root_cause=incident.root_cause,
        impact=sections.get("impact", "").strip() or "Not stated in the source.",
        resolution=incident.resolution,
        lessons_learned=tuple(_bullets(sections.get("lessons learned", ""))),
        written_at=incident.resolved_at or started_at,
        origin=origin,
    )
    return Imported(incident=incident, postmortem=postmortem, warnings=tuple(warnings))


def import_file(
    path: Path,
    repo: SqliteRepository,
    *,
    origin: str | None = None,
    keep_dir: Path | None = None,
) -> Imported:
    """Parse, store, and (optionally) keep a copy so `seed` re-imports it."""
    imported = parse(path.read_text(), origin=origin)
    existing = repo.incident(imported.incident.incident_id)
    if existing is not None and existing.origin == SYNTHETIC:
        raise MarkdownImportError(
            f"{imported.incident.incident_id} is an authored corpus id; pick another."
        )
    repo.upsert_incident(imported.incident)
    repo.upsert_postmortem(imported.postmortem)
    if keep_dir is not None:
        keep_dir.mkdir(parents=True, exist_ok=True)
        target = keep_dir / f"{imported.incident.incident_id}.md"
        if path.resolve() != target.resolve():
            target.write_text(_with_origin(path.read_text(), imported.incident.origin))
    return imported


def _with_origin(text: str, origin: str) -> str:
    """The kept copy records the origin it was imported under, so `seed` agrees."""
    _, header, body = text.split("---", 2)
    lines = [l for l in header.strip().splitlines() if not l.lower().startswith("origin:")]
    return "---\n" + "\n".join([*lines, f"origin: {origin}"]) + "\n---" + body


def reimport_all(directory: Path, repo: SqliteRepository) -> list[Imported]:
    if not directory.is_dir():
        return []
    return [import_file(path, repo) for path in sorted(directory.glob("*.md"))]


def _split(text: str) -> tuple[dict[str, str], dict[str, str]]:
    if not text.startswith("---"):
        raise MarkdownImportError("Expected front matter between `---` lines at the top.")
    try:
        _, header, body = text.split("---", 2)
    except ValueError as exc:
        raise MarkdownImportError("Front matter is not closed with `---`.") from exc

    front: dict[str, str] = {}
    for line in header.strip().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            front[key.strip().lower()] = value.strip()

    sections: dict[str, str] = {}
    current: str | None = None
    for line in body.splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            current = heading.group(1).strip().lower()
            sections[current] = ""
        elif current:
            sections[current] += line + "\n"
    return front, sections


def _bullets(text: str) -> list[str]:
    return [
        re.sub(r"^[-*]\s+", "", line.strip())
        for line in text.splitlines()
        if re.match(r"^\s*[-*]\s+", line)
    ]


def _csv(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def _events(
    text: str, started_at: datetime, service: str
) -> tuple[list[TimelineEvent], list[str]]:
    events: list[TimelineEvent] = []
    warnings: list[str] = []
    for line in _bullets(text):
        explicit = _EXPLICIT_EVENT.match(line)
        if explicit:
            raw_at = explicit["at"]
            at = (
                datetime.fromisoformat(raw_at.replace(" ", "T"))
                if "-" in raw_at
                else datetime.combine(
                    started_at.date(), datetime.strptime(raw_at, "%H:%M").time()
                )
            )
            events.append(
                TimelineEvent(
                    at=at,
                    type=EventType(explicit["type"]),
                    service=explicit["service"] or service,
                    description=explicit["desc"].strip(),
                )
            )
            continue
        proposed = log_events.extract(line, service=service, on_date=started_at.date())
        if proposed.events:
            event = proposed.events[0]
            events.append(
                TimelineEvent(
                    at=event.at, type=event.type, service=event.service, description=line
                )
            )
        else:
            warnings.append(f"Timeline line not understood, skipped: {line[:80]}")
    return sorted(events, key=lambda e: e.at), warnings


def _actions(text: str) -> tuple[list[ResolutionAction], list[str]]:
    actions: list[ResolutionAction] = []
    warnings: list[str] = []
    for line in _bullets(text):
        match = _ACTION.match(line)
        if not match:
            warnings.append(f"Action line not understood, skipped: {line[:80]}")
            continue
        actions.append(
            ResolutionAction(
                component=match["component"],
                target=match["target"],
                direction=ActionDirection(match["direction"]),
                from_value=match["from"],
                to_value=match["to"],
            )
        )
    return actions, warnings
