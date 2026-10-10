from datetime import datetime, timedelta

from app.analysis import temporal
from app.domain import EventType, TimelineEvent


def events(anchor: str, *pairs: tuple[int, str]) -> tuple[TimelineEvent, ...]:
    """Build a timeline from (minutes after `anchor`, event type) pairs."""
    base = datetime.fromisoformat(anchor)
    return tuple(
        TimelineEvent(
            at=base + timedelta(minutes=offset),
            type=EventType(kind),
            service="svc",
            description=kind,
        )
        for offset, kind in pairs
    )


DEPLOY_SHAPE = ((0, "deploy"), (6, "error_spike"), (8, "latency_spike"), (11, "alert"))


def test_anchor_is_the_first_deploy():
    anchor, normalized = temporal.normalize(events("2026-09-27T14:02:00", *DEPLOY_SHAPE))

    assert anchor == "deploy"
    assert [e.offset_minutes for e in normalized] == [0, 6, 8, 11]


def test_anchor_falls_back_to_incident_start_without_a_deploy():
    anchor, normalized = temporal.normalize(
        events("2026-09-27T09:31:00", (0, "dependency_timeout"), (4, "error_spike"))
    )

    assert anchor == "start"
    assert [e.offset_minutes for e in normalized] == [0, 4]


def test_identical_shapes_hours_apart_still_align():
    current = events("2026-09-27T14:02:00", *DEPLOY_SHAPE)
    historical = events("2026-08-14T03:41:00", *DEPLOY_SHAPE)

    alignment = temporal.align(current, historical)

    assert alignment.sequence_score == 1.0
    assert alignment.interval_score == 1.0
    assert alignment.anchor == "deploy"
    assert all(pair.drift_minutes == 0 for pair in alignment.matched)


def test_similar_shape_with_drift_scores_high_on_sequence_and_lower_on_interval():
    current = events("2026-09-27T14:02:00", *DEPLOY_SHAPE)
    historical = events(
        "2026-08-14T10:31:00",
        (0, "deploy"),
        (7, "error_spike"),
        (10, "latency_spike"),
        (18, "alert"),
    )

    alignment = temporal.align(current, historical)

    assert alignment.sequence_score == 1.0
    assert 0.5 < alignment.interval_score < 1.0
    assert max(pair.drift_minutes for pair in alignment.matched) == 7


def test_different_causal_shape_scores_lower_than_the_matching_one():
    current = events("2026-09-27T14:02:00", *DEPLOY_SHAPE)
    matching = events("2026-08-14T10:31:00", *DEPLOY_SHAPE)
    unrelated = events(
        "2026-08-20T23:50:00", (0, "alert"), (30, "latency_spike"), (80, "mitigation")
    )

    assert (
        temporal.align(current, unrelated).sequence_score
        < temporal.align(current, matching).sequence_score
    )


def test_a_timeline_with_no_events_is_not_comparable():
    alignment = temporal.align((), events("2026-08-14T10:31:00", *DEPLOY_SHAPE))

    assert alignment.comparable is False
    assert alignment.sequence_score == 0.0
    assert alignment.interval_score == 0.0
    assert temporal.describe(alignment) == "No timeline to compare."


def test_mixed_anchors_do_not_earn_interval_credit():
    """Offsets from a deploy and offsets from an incident start are not the same axis."""
    with_deploy = events("2026-09-27T14:02:00", *DEPLOY_SHAPE)
    without_deploy = events(
        "2026-09-02T09:14:00", (0, "error_spike"), (2, "latency_spike"), (5, "alert")
    )

    alignment = temporal.align(with_deploy, without_deploy)

    assert alignment.anchor == "mixed"
    assert alignment.sequence_score > 0
    assert alignment.interval_score == 0.0


def test_real_corpus_pair_aligns_better_than_an_unrelated_case(repo, eval_cases):
    current = eval_cases["CUR-2051"].incident

    match = temporal.align(current.events, repo.incident("INC-1042").events)
    unrelated = temporal.align(current.events, repo.incident("INC-1150").events)

    assert match.sequence_score > 0.8
    assert match.interval_score > 0.7
    assert match.sequence_score > unrelated.sequence_score
