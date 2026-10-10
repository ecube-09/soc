"""Retrieval behaviour, asserted without the model in the way.

The provider here is a hashing stand-in, so these tests pin the ranking logic
rather than the quality of any particular embedding model.
"""

import pytest

from app.domain import ResponseMode
from app.retrieval import query_text


@pytest.fixture
async def pack(retriever, eval_cases):
    return await retriever.build_evidence(eval_cases["CUR-2051"].incident)


async def test_matching_case_outranks_every_other(pack):
    assert pack.top.incident.incident_id == "INC-1042"
    assert "same-service" in pack.top.flags
    assert "timeline-match" in pack.top.flags


async def test_staging_distractor_is_filtered_out_and_reported(pack):
    retrieved = {c.incident.incident_id for c in pack.candidates}
    excluded = {e.incident_id: e for e in pack.excluded}

    assert "INC-0995" not in retrieved
    assert "staging" in excluded["INC-0995"].reason


async def test_stale_near_duplicate_is_kept_but_flagged_and_ranked_lower(pack):
    stale = next(c for c in pack.candidates if c.incident.incident_id == "INC-0412")

    assert stale.is_stale
    assert stale.recency < 0.01
    assert stale.score < pack.top.score
    # It was not demoted for reading differently; the text matches just as well.
    assert stale.timeline.sequence_score >= pack.top.timeline.sequence_score


async def test_every_score_component_is_reported_alongside_the_total(pack):
    components = pack.top.to_dict()["components"]

    assert set(components) == {
        "semantic",
        "metadata",
        "timeline_sequence",
        "timeline_interval",
        "recency",
        "service_graph",
    }
    assert all(0.0 <= value <= 1.0 for value in components.values())


async def test_metadata_reasons_explain_the_match_in_words(pack):
    reasons = " ".join(pack.top.metadata_reasons)

    assert "same service (payment-api)" in reasons
    assert "same environment (production)" in reasons
    assert "redis-cache" in reasons


async def test_conflict_is_surfaced_and_charged_against_confidence(pack):
    targets = {c.target for c in pack.conflicts}

    assert targets == {"redis_connection_pool_size"}
    assert pack.mode is ResponseMode.INVESTIGATION
    assert pack.confidence < pack.top.score
    assert any("conflicts" in unknown for unknown in pack.unknowns)


async def test_citation_ids_cover_incidents_postmortems_logs_and_runbooks(pack):
    ids = pack.citation_ids()

    assert "INC-1042" in ids
    assert "PM-1042" in ids
    assert "LOG-1042" in ids
    assert any(i.startswith("RB-") for i in ids)


async def test_weak_case_escalates_before_anything_is_generated(retriever, eval_cases):
    pack = await retriever.build_evidence(eval_cases["CUR-2052"].incident)

    assert pack.mode is ResponseMode.ESCALATION
    assert pack.confidence < 0.45
    assert pack.unknowns
    assert any("not in the service map" in note for note in pack.notes)


async def test_dependency_case_reaches_the_candidate_set_through_the_service_map(
    retriever, eval_cases
):
    pack = await retriever.build_evidence(eval_cases["CUR-2054"].incident)
    by_id = {c.incident.incident_id: c for c in pack.candidates}

    assert pack.top.incident.incident_id == "INC-0778"
    postgres_cases = [c for c in by_id.values() if c.incident.service != "auth-api"]
    assert postgres_cases, "one-hop dependency cases should be considered"
    assert all(c.service_graph > 0 for c in postgres_cases)


async def test_runbook_is_chosen_on_its_trigger_not_only_its_service(
    retriever, eval_cases
):
    pack = await retriever.build_evidence(eval_cases["CUR-2053"].incident)

    assert pack.runbooks[0].runbook.runbook_id == "RB-31"
    assert "trigger matches" in pack.runbooks[0].reason


async def test_query_text_carries_the_timeline_shape_not_just_the_prose(eval_cases):
    text = query_text(eval_cases["CUR-2051"].incident)

    assert "timeline anchored on deploy" in text
    assert "deploy+0m -> error_spike+7m" in text
    assert "dependencies: redis-cache" in text


async def test_ranking_is_stable_across_runs(retriever, eval_cases):
    first = await retriever.build_evidence(eval_cases["CUR-2055"].incident)
    second = await retriever.build_evidence(eval_cases["CUR-2055"].incident)

    assert [c.incident.incident_id for c in first.candidates] == [
        c.incident.incident_id for c in second.candidates
    ]
    assert first.confidence == second.confidence


async def test_age_is_measured_against_the_incident_not_the_wall_clock(
    retriever, eval_cases
):
    """A case reopened months later must not silently rerank."""
    pack = await retriever.build_evidence(eval_cases["CUR-2051"].incident)
    top = next(c for c in pack.candidates if c.incident.incident_id == "INC-1042")

    assert top.age_days == 44
