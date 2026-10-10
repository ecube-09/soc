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
    Finding,
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
    score: float,
    *,
    flags=(),
    events=None,
    incident_id="INC-9001",
    age_days=10,
    related=(),
):
    events = timeline() if events is None else events
    incident = Incident(
        related_services=related,
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


def test_conflict_is_charged_against_confidence_but_stated_only_once(settings):
    """The conflict lives in the conflict list; repeating it as an unknown is noise."""
    decision = decide(settings, conflicts=[conflict()])

    assert decision.confidence == pytest.approx(0.85 - settings.conflict_confidence_penalty)
    assert "unresolved conflicting remediation" in decision.reason
    assert not any("measure utilisation first" in u for u in decision.unknowns)


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

    assert decision.supporting_ids == ("INC-9001", "INC-9002")
    assert "1 other case(s) share its failure shape: INC-9002" in decision.reason


def test_same_service_with_a_different_dependency_is_not_support(settings):
    """A payment-api slow query is a neighbour of a payment-api Redis outage, not support."""
    redis = candidate(0.85, incident_id="INC-9001", related=("redis-cache",))
    postgres = candidate(0.84, incident_id="INC-9002", related=("postgres-primary",))
    also_redis = candidate(0.80, incident_id="INC-9003", related=("redis-cache",))

    decision = decide(settings, candidates=[redis, postgres, also_redis])

    assert decision.supporting_ids == ("INC-9001", "INC-9003")
    assert "INC-9002" not in decision.reason


def test_a_different_timeline_shape_is_not_support(settings):
    other_shape = tuple(
        TimelineEvent(
            at=datetime(2026, 9, 1, 10, 0) + timedelta(minutes=i),
            type=EventType.MITIGATION,
            service="payment-api",
            description="",
        )
        for i in range(3)
    )
    decision = decide(
        settings,
        candidates=[
            candidate(0.85, incident_id="INC-9001"),
            candidate(0.84, incident_id="INC-9002", events=other_shape),
        ],
    )

    assert decision.supporting_ids == ("INC-9001",)
    assert "no other case shares its failure shape" in decision.reason


def finding(favours: str, observation: str = "pool=50/50 on every replica"):
    return Finding(
        target="redis_connection_pool_size",
        component="payment-api",
        favours_incident_id=favours,
        observation=observation,
        author="asha",
    )


def test_a_finding_for_the_top_case_removes_the_conflict_penalty(settings):
    decision = decide(settings, conflicts=[conflict()], findings=(finding("INC-9001"),))

    assert decision.confidence == pytest.approx(0.85)
    assert decision.resolutions[0].favours_incident_id == "INC-9001"
    assert "settled by a recorded measurement in favour of INC-9001" in decision.reason


def test_a_finding_against_the_top_case_escalates(settings):
    """The measurement says the other side applies, so the leading match does not fit."""
    decision = decide(settings, conflicts=[conflict()], findings=(finding("INC-9002"),))

    assert decision.mode is ResponseMode.ESCALATION
    assert decision.confidence == pytest.approx(0.85 * settings.contradicted_top_factor)
    assert "favours INC-9002 over INC-9001" in decision.reason
    assert any("pool=50/50" in u for u in decision.unknowns)


def test_a_finding_can_lift_an_escalation_back_into_an_investigation(settings):
    unresolved = decide(settings, candidates=[candidate(0.55)], conflicts=[conflict()])
    settled = decide(
        settings,
        candidates=[candidate(0.55)],
        conflicts=[conflict()],
        findings=(finding("INC-9001"),),
    )

    assert unresolved.mode is ResponseMode.ESCALATION
    assert settled.mode is ResponseMode.INVESTIGATION


def test_the_latest_finding_on_a_knob_wins(settings):
    decision = decide(
        settings,
        conflicts=[conflict()],
        findings=(finding("INC-9002"), finding("INC-9001", "re-measured")),
    )

    assert decision.mode is ResponseMode.INVESTIGATION
    assert decision.resolutions[0].observation == "re-measured"


def test_a_finding_naming_neither_side_is_not_applied(settings):
    decision = decide(settings, conflicts=[conflict()], findings=(finding("INC-4242"),))

    assert decision.confidence == pytest.approx(0.85 - settings.conflict_confidence_penalty)
    assert decision.resolutions == ()
    assert "not a side of this conflict" in decision.notes[0]
