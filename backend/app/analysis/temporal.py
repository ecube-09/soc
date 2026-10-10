"""Timeline alignment.

Two incidents can start hours apart and still be the same story. What matters is
the order events arrived in and the gaps between them, so both timelines are
anchored on their first deploy (or on incident start when nothing was deployed)
and compared as offsets from there.

Both scores are returned separately, and the matched pairs come with them, so a
comparable case can be inspected rather than trusted.
"""

from dataclasses import dataclass
from difflib import SequenceMatcher

from app.domain import EventType, TimelineEvent

# Minutes of drift at which an interval match is worth half credit.
INTERVAL_TOLERANCE_MINUTES = 5.0


@dataclass(frozen=True)
class NormalizedEvent:
    type: EventType
    offset_minutes: int
    service: str
    description: str


@dataclass(frozen=True)
class MatchedPair:
    type: EventType
    current_offset: int
    historical_offset: int

    @property
    def drift_minutes(self) -> int:
        return abs(self.current_offset - self.historical_offset)


@dataclass(frozen=True)
class TimelineAlignment:
    sequence_score: float
    interval_score: float
    anchor: str
    current: tuple[NormalizedEvent, ...]
    historical: tuple[NormalizedEvent, ...]
    matched: tuple[MatchedPair, ...]

    @property
    def comparable(self) -> bool:
        return bool(self.current) and bool(self.historical)

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence_score": round(self.sequence_score, 3),
            "interval_score": round(self.interval_score, 3),
            "anchor": self.anchor,
            "current": [_event_dict(e) for e in self.current],
            "historical": [_event_dict(e) for e in self.historical],
            "matched": [
                {
                    "type": pair.type.value,
                    "current_offset": pair.current_offset,
                    "historical_offset": pair.historical_offset,
                    "drift_minutes": pair.drift_minutes,
                }
                for pair in self.matched
            ],
        }


def normalize(events: tuple[TimelineEvent, ...]) -> tuple[str, tuple[NormalizedEvent, ...]]:
    """Return the anchor used and the events as minute offsets from it."""
    if not events:
        return "none", ()

    ordered = sorted(events, key=lambda e: e.at)
    deploys = [e for e in ordered if e.type is EventType.DEPLOY]
    anchor_event = deploys[0] if deploys else ordered[0]
    anchor = "deploy" if deploys else "start"

    return anchor, tuple(
        NormalizedEvent(
            type=event.type,
            offset_minutes=int((event.at - anchor_event.at).total_seconds() // 60),
            service=event.service,
            description=event.description,
        )
        for event in ordered
    )


def align(
    current: tuple[TimelineEvent, ...], historical: tuple[TimelineEvent, ...]
) -> TimelineAlignment:
    current_anchor, current_events = normalize(current)
    historical_anchor, historical_events = normalize(historical)

    # Anchoring one timeline on a deploy and the other on its start would compare
    # two different things, so the shapes are only judged when both agree.
    anchor = current_anchor if current_anchor == historical_anchor else "mixed"

    if not current_events or not historical_events:
        return TimelineAlignment(
            sequence_score=0.0,
            interval_score=0.0,
            anchor=anchor,
            current=current_events,
            historical=historical_events,
            matched=(),
        )

    current_types = [e.type.value for e in current_events]
    historical_types = [e.type.value for e in historical_events]
    matcher = SequenceMatcher(None, current_types, historical_types, autojunk=False)

    matched: list[MatchedPair] = []
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            left = current_events[block.a + offset]
            right = historical_events[block.b + offset]
            matched.append(
                MatchedPair(
                    type=left.type,
                    current_offset=left.offset_minutes,
                    historical_offset=right.offset_minutes,
                )
            )

    interval_score = 0.0
    if matched and anchor != "mixed":
        interval_score = sum(
            INTERVAL_TOLERANCE_MINUTES
            / (INTERVAL_TOLERANCE_MINUTES + pair.drift_minutes)
            for pair in matched
        ) / len(matched)

    return TimelineAlignment(
        sequence_score=matcher.ratio(),
        interval_score=interval_score,
        anchor=anchor,
        current=current_events,
        historical=historical_events,
        matched=tuple(matched),
    )


def describe(alignment: TimelineAlignment) -> str:
    """One line an engineer can check against the workspace timeline."""
    if not alignment.comparable:
        return "No timeline to compare."

    shape = " -> ".join(_dedupe(e.type.value for e in alignment.current))
    if not alignment.matched:
        return f"Current shape {shape}; no shared events with this case."

    drift = max(pair.drift_minutes for pair in alignment.matched)
    return (
        f"Shared shape {' -> '.join(_dedupe(p.type.value for p in alignment.matched))}"
        f", anchored on {alignment.anchor}, widest gap difference {drift} min."
    )


def _dedupe(values) -> list[str]:
    out: list[str] = []
    for value in values:
        if not out or out[-1] != value:
            out.append(value)
    return out


def _event_dict(event: NormalizedEvent) -> dict[str, object]:
    return {
        "type": event.type.value,
        "offset_minutes": event.offset_minutes,
        "service": event.service,
        "description": event.description,
    }
