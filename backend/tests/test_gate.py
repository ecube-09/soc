"""The gate is the rule the project turns on, so it is tested as a unit."""

from datetime import datetime, timedelta

import pytest

from app.analysis import gate
from app.analysis.conflicts import ConflictingFix, ConflictSide
from app.analysis.evidence import CandidateCase, RunbookMatch
from app.analysis.temporal import align
from app.domain import (
    ActionDirection,
    Environment,
    EventType,
    Incident,
    ResolutionAction,
    ResponseMode,
    Runbook,
    Severity,
    TimelineEvent,
)


SHAPE = (
    (0, "deploy"),
    (6, "error_spike"),
    (8, "latency_spike"),
    (11, "alert"),
)


def timeline():
    base = datetime(2026, 9, 1, 10, 0)
    return tuple(
        TimelineEvent(
            at=base + timedelta(minutes=offset),
            type=EventType(kind),
            service="payment-api",
            description=kind,
        )
        for offset, kind in SHAPE
    )


def candidate(
    score: float, *, flags=(), events=None, incident_id="INC-9001", age_days=10
):
    events = timeline() if events is None else events
    incident = Incident(
        incident_id=incident_id,
        title="Test case",
        summary="",
        severity=Severity.SEV2,
        service="payment-api",
        environment=Environment.PRODUCTION,
        region="ap-south-1",
        started_at=datetime(2026, 9, 1, 10, 0),
        resolved_at=None,
        root_cause="",
        resolution="",
        tech_context=("Redis 5.0",),
        events=events,
    )
    return CandidateCase(
        incident=incident,
        semantic=score,
        metadata_score=score,
        metadata_reasons=(),
        timeline=align(events, events),
        recency=0.8,
        service_graph=1.0,
        graph_reason=None,
        score=score,
        age_days=age_days,
        flags=tuple(flags),
    )


def runbook_match(escalate_to: str | None = "platform"):
    return RunbookMatch(
        runbook=Runbook(
            runbook_id="RB-01",
            title="Test runbook",
            trigger="",
            service="payment-api",
            steps=("check something",),
            escalate_to=escalate_to,
            version="1.0",
            updated_at=datetime(2026, 9, 1),
        ),
        semantic=0.8,
        score=0.8,
        reason="",
    )


def conflict(incident_ids=("INC-9001", "INC-9002")):
    side = lambda incident_id, direction: ConflictSide(  # noqa: E731
        incident_id=incident_id,
        title="",
        action=ResolutionAction(
            component="payment-api",
            target="redis_connection_pool_size",
            direction=direction,
        ),
        root_cause="",
        age_days=10,
    )
    return ConflictingFix(
        target="redis_connection_pool_size",
        component="payment-api",
        summary="one raised it, the other lowered it",
        distinguishing_question="measure utilisation first",
        more_recent_incident_id=incident_ids[1],
        sides=(
            side(incident_ids[0], ActionDirection.INCREASE),
            side(incident_ids[1], ActionDirection.DECREASE),
        ),
    )


def decide(settings, **kwargs):
    defaults = {
        "has_timeline": True,
        "candidates": [candidate(0.85)],
        "runbooks": [runbook_match()],
        "conflicts": [],
    }
    return gate.evaluate(settings=settings, **{**defaults, **kwargs})


def test_strong_evidence_yields_an_investigation(settings):
    decision = decide(settings)

    assert decision.mode is ResponseMode.INVESTIGATION
    assert decision.confidence == pytest.approx(0.85)
    assert "INC-9001 matches at 0.85" in decision.reason


def test_no_candidates_escalates_and_says_so_plainly(settings):
    decision = decide(settings, candidates=[])

    assert decision.mode is ResponseMode.ESCALATION
    assert decision.confidence == 0.0
    assert "No comparable historical incident" in decision.reason
    assert "cannot suggest a cause" in decision.unknowns[0]


def test_weak_top_match_escalates(settings):
    decision = decide(settings, candidates=[candidate(0.30)])

    assert decision.mode is ResponseMode.ESCALATION
    assert "below the 0.45 threshold" in decision.reason
    assert "matches at only 0.30" in decision.unknowns[0]


def test_stale_top_match_is_discounted_and_recorded_as_an_unknown(settings):
    decision = decide(
        settings, candidates=[candidate(0.70, flags=("stale",), age_days=1600)]
    )

    assert decision.confidence == pytest.approx(0.42)
    assert decision.mode is ResponseMode.ESCALATION
    assert "predates the current stack" in decision.reason
    assert "Redis 5.0" in decision.unknowns[0]


def test_missing_timeline_is_discounted_and_named(settings):
    decision = decide(settings, has_timeline=False)

    assert decision.confidence == pytest.approx(0.68)
    assert decision.mode is ResponseMode.INVESTIGATION
    assert "No event timeline was provided" in decision.unknowns[0]


def test_conflict_is_charged_against_confidence_with_the_deciding_question(settings):
    decision = decide(settings, conflicts=[conflict()])

    assert decision.confidence == pytest.approx(0.85 - settings.conflict_confidence_penalty)
    assert "unresolved conflicting remediation" in decision.reason
    assert "measure utilisation first" in decision.unknowns[0]


def test_conflicts_can_push_a_good_match_over_into_escalation(settings):
    decision = decide(
        settings,
        candidates=[candidate(0.60)],
        conflicts=[conflict(), conflict(("INC-9001", "INC-9003"))],
    )

    assert decision.mode is ResponseMode.ESCALATION
    assert decision.confidence == pytest.approx(0.30)


def test_conflict_between_cases_nobody_retrieved_is_ignored(settings):
    decision = decide(settings, conflicts=[conflict(("INC-7000", "INC-7001"))])

    assert decision.confidence == pytest.approx(0.85)
    assert decision.unknowns == ()


def test_missing_runbook_is_discounted_and_named(settings):
    decision = decide(settings, runbooks=[])

    assert decision.confidence == pytest.approx(0.765)
    assert "no runbook matched" in decision.reason
    assert any("no documented procedure" in u for u in decision.unknowns)


def test_escalation_target_comes_from_the_matched_runbook(settings):
    assert decide(settings, candidates=[]).escalate_to == "platform"
    assert decide(settings, candidates=[], runbooks=[]).escalate_to is None


def test_threshold_is_configuration_not_a_constant(settings):
    strict = settings.model_copy(update={"confidence_threshold": 0.9})

    assert decide(settings).mode is ResponseMode.INVESTIGATION
    assert decide(strict).mode is ResponseMode.ESCALATION


def test_supporting_count_only_includes_cases_near_the_top_score(settings):
    decision = decide(
        settings,
        candidates=[
            candidate(0.85, incident_id="INC-9001"),
            candidate(0.80, incident_id="INC-9002"),
            candidate(0.50, incident_id="INC-9003"),
        ],
    )

    assert "2 supporting case(s)" in decision.reason
