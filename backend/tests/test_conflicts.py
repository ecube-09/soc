from app.analysis import conflicts


def test_detects_the_pool_size_pair(repo):
    found = conflicts.detect(repo.incidents())

    pool = [c for c in found if c.target == "redis_connection_pool_size"]
    pair = next(c for c in pool if set(c.incident_ids) == {"INC-1042", "INC-1198"})

    assert pair.component == "payment-api"
    assert [side.action.direction.value for side in pair.sides] == [
        "increase",
        "decrease",
    ]
    assert pair.more_recent_incident_id == "INC-1198"
    assert "50 -> 100" in pair.summary and "100 -> 30" in pair.summary


def test_conflict_carries_the_question_that_decides_between_the_two(repo):
    pair = next(
        c
        for c in conflicts.detect(repo.incidents())
        if set(c.incident_ids) == {"INC-1042", "INC-1198"}
    )

    assert "before moving" in pair.distinguishing_question
    # Each side keeps its own root cause, which is what actually distinguishes
    # app-side exhaustion from dependency-side saturation.
    causes = {side.incident_id: side.root_cause for side in pair.sides}
    assert "idempotency lookup" in causes["INC-1042"]
    assert "maxclients" in causes["INC-1198"]


def test_detects_the_worker_scaling_pair(repo):
    found = conflicts.detect(repo.incidents())

    pair = next(c for c in found if c.target == "worker_replica_count")

    assert set(pair.incident_ids) == {"INC-1120", "INC-1320"}
    assert [side.action.direction.value for side in pair.sides] == [
        "scale_out",
        "scale_in",
    ]


def test_same_direction_on_the_same_knob_is_not_a_conflict(repo):
    """INC-0871 and INC-1150 both raise things; agreement is not disagreement."""
    found = conflicts.detect(repo.incidents())

    assert all(
        {side.action.direction for side in c.sides}.__len__() == 2 for c in found
    )
    assert not [c for c in found if c.target == "pgbouncer_pool_size"]


def test_rollback_and_failover_are_never_treated_as_opposites(repo):
    found = conflicts.detect(repo.incidents())

    assert not [c for c in found if c.target == "deployment_version"]
    assert not [c for c in found if c.target in {"postgres_primary", "redis_primary"}]


def test_scoping_drops_conflicts_on_unrelated_components(repo):
    incidents = repo.incidents()
    candidates = {"INC-1042", "INC-1198", "INC-1320"}

    scoped = conflicts.conflicts_for(
        incidents, candidates, {"payment-api", "redis-cache"}
    )

    assert {c.target for c in scoped} == {"redis_connection_pool_size"}


def test_scoping_drops_conflicts_nobody_retrieved(repo):
    scoped = conflicts.conflicts_for(
        repo.incidents(), {"INC-1255"}, {"payment-api", "ledger-service"}
    )

    assert scoped == []
