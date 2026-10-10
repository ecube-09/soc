"""Propose typed timeline events from a pasted log.

The temporal score only works on typed events, and a pasted log has none. This
reads timestamps and a handful of operational patterns and proposes events for
the engineer to accept or discard. It never adds anything to an incident on its
own, and it is rules rather than a model, so the same paste always proposes the
same events and every proposal names the rule that produced it.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.domain import EventType

_ISO = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}(?::\d{2})?)"
)
_CLOCK = re.compile(r"(?<![\d:])(?P<time>\d{1,2}:\d{2}(?::\d{2})?)(?![\d:])")

# Ordered: the first matching rule wins. Mitigation and deploy come before the
# error patterns because "rolled back after 503s" is a mitigation, not a spike.
_RULES: tuple[tuple[EventType, str, re.Pattern[str]], ...] = (
    (
        EventType.MITIGATION,
        "mitigation keyword",
        re.compile(
            r"\b(rolled back|rollback|reverted|restart(ed|ing)?|failover|failed over|"
            r"promot(ed|ing)|scaled (out|in|up|down)|drain(ed|ing)|mitigat\w*|"
            r"pool (size )?(raised|increased|lowered|decreased))\b",
            re.IGNORECASE,
        ),
    ),
    (
        EventType.DEPLOY,
        "deploy keyword",
        re.compile(
            r"\b(deploy(ed|ing|ment)?|rollout|rolled out|releas(e|ed|ing) v?\d|"
            r"helm upgrade|image updated|new version)\b",
            re.IGNORECASE,
        ),
    ),
    (
        EventType.ALERT,
        "alert keyword",
        re.compile(r"\b(alert\w*|paged|pagerduty|firing|fired|on-call)\b", re.IGNORECASE),
    ),
    (
        EventType.DEPENDENCY_TIMEOUT,
        "timeout or refused connection",
        re.compile(
            r"(timeout|timed out|connection refused|econnrefused|no route to host|"
            r"unreachable|max number of clients|waiting for connection|"
            r"no more connections|handshake fail\w*)",
            re.IGNORECASE,
        ),
    ),
    (
        EventType.ERROR_SPIKE,
        "error status or level",
        re.compile(
            r"(\b5\d\d\b|\b5xx\b|\berror rate\b|\bexception\b|\b(ERROR|FATAL|CRIT)\b)"
        ),
    ),
    (
        EventType.LATENCY_SPIKE,
        "latency keyword",
        re.compile(
            r"\b(latency|p9[59]|slow|wait \d+ ?ms|took \d+ ?ms|\d{4,} ?ms)\b",
            re.IGNORECASE,
        ),
    ),
)

# Words in a log line that name a dependency, mapped to the first matching
# service in the service map.
_ALIASES = {
    "redis": ("redis",),
    "postgres": ("postgres", "pg", "patroni", "pgbouncer", "psql", "db"),
    "ledger": ("ledger",),
    "auth": ("auth",),
}

# Repeats of one type closer than this are one event: the onset is what aligns.
_COLLAPSE_MINUTES = 2


@dataclass(frozen=True)
class ProposedEvent:
    at: datetime
    type: EventType
    service: str
    description: str
    source_line: str
    rule: str

    def to_dict(self) -> dict[str, object]:
        return {
            "at": self.at.isoformat(timespec="minutes"),
            "type": self.type.value,
            "service": self.service,
            "description": self.description,
            "source_line": self.source_line,
            "rule": self.rule,
        }


@dataclass(frozen=True)
class Extraction:
    events: tuple[ProposedEvent, ...]
    lines_read: int
    lines_without_time: int
    lines_unclassified: int

    def to_dict(self) -> dict[str, object]:
        return {
            "events": [e.to_dict() for e in self.events],
            "lines_read": self.lines_read,
            "lines_without_time": self.lines_without_time,
            "lines_unclassified": self.lines_unclassified,
        }


def extract(
    log_text: str,
    *,
    service: str,
    known_services: tuple[str, ...] = (),
    on_date: date | None = None,
) -> Extraction:
    base_day = on_date or date.today()
    proposals: list[ProposedEvent] = []
    without_time = unclassified = read = 0
    previous_at: datetime | None = None

    for raw in log_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        read += 1

        at, rest = _timestamp(line, base_day)
        if at is None:
            without_time += 1
            continue
        # A clock-only log that crosses midnight keeps moving forward.
        if previous_at and at < previous_at - timedelta(hours=12):
            at += timedelta(days=1)
        previous_at = at

        classified = _classify(rest)
        if classified is None:
            unclassified += 1
            continue
        event_type, rule = classified
        proposals.append(
            ProposedEvent(
                at=at,
                type=event_type,
                service=_service_for(rest, event_type, service, known_services),
                description=_describe(rest),
                source_line=line,
                rule=rule,
            )
        )

    return Extraction(
        events=tuple(_collapse(sorted(proposals, key=lambda e: e.at))),
        lines_read=read,
        lines_without_time=without_time,
        lines_unclassified=unclassified,
    )


def _timestamp(line: str, base_day: date) -> tuple[datetime | None, str]:
    match = _ISO.search(line)
    if match:
        at = datetime.fromisoformat(f"{match['date']}T{_pad(match['time'])}")
        return at, (line[: match.start()] + line[match.end() :]).strip()
    match = _CLOCK.search(line[:20])
    if match:
        hh, mm, *ss = (int(p) for p in match["time"].split(":"))
        if hh > 23 or mm > 59:
            return None, line
        at = datetime.combine(base_day, datetime.min.time()).replace(
            hour=hh, minute=mm, second=ss[0] if ss else 0
        )
        return at, (line[: match.start()] + line[match.end() :]).strip()
    return None, line


def _pad(clock: str) -> str:
    return clock if clock.count(":") == 2 else f"{clock}:00"


def _classify(text: str) -> tuple[EventType, str] | None:
    for event_type, rule, pattern in _RULES:
        if pattern.search(text):
            return event_type, rule
    return None


def _service_for(
    text: str, event_type: EventType, default: str, known: tuple[str, ...]
) -> str:
    """Dependency events are attributed to the dependency the line names."""
    if event_type is not EventType.DEPENDENCY_TIMEOUT:
        return default
    lowered = text.lower()
    for stem, words in _ALIASES.items():
        if any(re.search(rf"\b{re.escape(w)}\b", lowered) for w in words):
            for name in known:
                if name.startswith(stem) and name != default:
                    return name
    return default


def _describe(text: str) -> str:
    cleaned = re.sub(r"^\s*(INFO|WARN(ING)?|ERROR|FATAL|CRIT|DEBUG)\s+", "", text)
    return cleaned[:160]


def _collapse(events: list[ProposedEvent]) -> list[ProposedEvent]:
    out: list[ProposedEvent] = []
    for event in events:
        if any(
            kept.type is event.type
            and kept.service == event.service
            and (event.at - kept.at) <= timedelta(minutes=_COLLAPSE_MINUTES)
            for kept in out[-3:]
        ):
            continue
        out.append(event)
    return out
