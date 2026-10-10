"""The corpus is also the eval set, so these assert the cases it was authored for."""

from app.domain import ActionDirection, Environment, directions_conflict


def test_seed_loads_every_source_type(repo):
    counts = repo.counts()

    assert counts["services"] >= 8
    assert counts["incidents"] >= 15
    assert counts["runbooks"] >= 8
    assert counts["postmortems"] >= 10
    assert counts["events"] > counts["incidents"]
    assert counts["resolution_actions"] >= counts["incidents"]


def test_matching_case_carries_its_timeline_and_actions(repo):
    incident = repo.incident("INC-1042")

    assert incident is not None
    assert incident.service == "payment-api"
    assert incident.environment is Environment.PRODUCTION
    assert incident.duration_minutes == 43
    assert [e.type.value for e in incident.events][:4] == [
        "deploy",
        "error_spike",
        "latency_spike",
        "dependency_timeout",
    ]
    assert any(a.target == "redis_connection_pool_size" for a in incident.actions)


def test_conflicting_pair_moves_the_same_knob_in_opposite_directions(repo):
    raised = next(
        a
        for a in repo.incident("INC-1042").actions
        if a.target == "redis_connection_pool_size"
    )
    lowered = next(
        a
        for a in repo.incident("INC-1198").actions
        if a.target == "redis_connection_pool_size"
    )

    assert raised.direction is ActionDirection.INCREASE
    assert lowered.direction is ActionDirection.DECREASE
    assert directions_conflict(raised.direction, lowered.direction)


def test_second_conflicting_pair_exists_for_worker_scaling(repo):
    out = next(
        a for a in repo.incident("INC-1120").actions if a.target == "worker_replica_count"
    )
    back_in = next(
        a for a in repo.incident("INC-1320").actions if a.target == "worker_replica_count"
    )

    assert directions_conflict(out.direction, back_in.direction)


def test_staging_distractor_shares_symptoms_with_the_production_case(repo):
    distractor = repo.incident("INC-0995")

    assert distractor.service == "payment-api"
    assert distractor.environment is Environment.STAGING
    assert any("503" in s for s in distractor.symptoms)


def test_stale_case_is_old_and_its_remediation_is_dated(repo):
    stale = repo.incident("INC-0412")
    current = repo.incident("INC-1042")

    assert stale.started_at.year == 2022
    assert (current.started_at - stale.started_at).days > 1000
    assert any("Redis 5.0" in c for c in stale.tech_context)


def test_service_map_records_payment_api_dependencies(repo):
    services = {s.name: s for s in repo.services()}

    assert set(services["payment-api"].dependencies) == {
        "redis-cache",
        "postgres-primary",
        "ledger-service",
    }
    assert services["payment-api"].team == "payments"


def test_every_postmortem_and_log_points_at_a_real_incident(repo):
    incident_ids = {i.incident_id for i in repo.incidents()}

    assert {p.incident_id for p in repo.postmortems()} <= incident_ids
    for incident_id in incident_ids:
        for log in repo.log_excerpts_for(incident_id):
            assert log.incident_id == incident_id


def test_eval_cases_cover_investigation_and_escalation(eval_cases):
    modes = {case.expected.mode for case in eval_cases.values()}

    assert modes == {"investigation", "escalation"}
    assert eval_cases["CUR-2052"].expected.mode == "escalation"
    assert eval_cases["CUR-2052"].incident.events == ()

    strong = eval_cases["CUR-2051"]
    assert strong.expected.incident_ids == ("INC-1042",)
    assert strong.expected.stale_ids == ("INC-0412",)
    assert strong.expected.excluded_ids == ("INC-0995",)
    assert strong.expected.conflict_targets == ("redis_connection_pool_size",)


def test_eval_case_labels_reference_real_corpus_ids(repo, eval_cases):
    incident_ids = {i.incident_id for i in repo.incidents()}
    runbook_ids = {r.runbook_id for r in repo.runbooks()}

    for case in eval_cases.values():
        expected = case.expected
        assert set(expected.incident_ids) <= incident_ids, case.case_id
        assert set(expected.stale_ids) <= incident_ids, case.case_id
        assert set(expected.excluded_ids) <= incident_ids, case.case_id
        assert set(expected.runbook_ids) <= runbook_ids, case.case_id
