"""Generation and the second pass of the gate.

The provider is stubbed with exact JSON so these test what the system does with
a model's output, including a model that misbehaves.
"""

import json

import pytest

from app.domain import ResponseMode, RootCauseStatus
from app.services.investigation import InvestigationService


def draft(**overrides) -> str:
    body = {
        "headline": "payment-api looks like INC-1042.",
        "root_cause_status": "hypothesis",
        "root_cause_hypothesis": "Redis client pool exhaustion after v2.8.1, as in INC-1042.",
        "steps": [
            {
                "action": "Read the client-side pool metrics per replica.",
                "rationale": "Separates app-side exhaustion from dependency saturation.",
                "would_falsify": "Pool checkouts well below the limit on every replica.",
                "citations": ["INC-1042", "RB-17"],
            }
        ],
        "cautions": ["INC-0412 is from the Redis 5 era; do not reuse its values."],
        "unresolved": ["Whether v2.8.1 changed the client library."],
        "requested_team": "payments",
    }
    body.update(overrides)
    return json.dumps(body)


@pytest.fixture
def service(retriever, repo, settings, provider):
    return InvestigationService(retriever, provider, repo, settings)


@pytest.fixture
def strong(eval_cases):
    return eval_cases["CUR-2051"].incident


@pytest.fixture
def weak(eval_cases):
    return eval_cases["CUR-2052"].incident


async def test_strong_evidence_produces_a_cited_investigation(
    service, provider, strong
):
    provider.responses = [draft()]

    result = await service.investigate(strong)

    assert result.mode is ResponseMode.INVESTIGATION
    assert result.root_cause_status is RootCauseStatus.HYPOTHESIS
    assert result.root_cause_hypothesis.startswith("Redis client pool exhaustion")
    assert result.steps[0].citations == ("INC-1042", "RB-17")
    assert result.escalation is None
    assert result.validation_notes == ()


async def test_invented_citation_is_dropped_and_reported(service, provider, strong):
    provider.responses = [
        draft(
            steps=[
                {
                    "action": "Check the pool.",
                    "rationale": "Because.",
                    "citations": ["INC-1042", "INC-9999", "RB-404"],
                }
            ]
        )
    ]

    result = await service.investigate(strong)

    assert result.steps[0].citations == ("INC-1042",)
    note = " ".join(result.validation_notes)
    assert "INC-9999" in note and "RB-404" in note
    assert "not in the retrieved evidence" in note


async def test_step_with_no_valid_citation_is_dropped(service, provider, strong):
    provider.responses = [
        draft(
            steps=[
                {
                    "action": "Restart everything.",
                    "rationale": "Hunch.",
                    "citations": ["INC-0000"],
                },
                {
                    "action": "Read the Redis connection pool metrics.",
                    "rationale": "Cited.",
                    "citations": ["INC-1042", "RB-17"],
                },
            ]
        )
    ]

    result = await service.investigate(strong)

    assert [s.action for s in result.steps] == ["Read the Redis connection pool metrics."]
    assert any("Restart everything" in n for n in result.validation_notes)


async def test_a_step_backed_only_by_a_runbook_is_dropped(service, provider, strong):
    provider.responses = [
        draft(
            steps=[
                {
                    "action": "Compare Redis connection pool usage against INC-1042.",
                    "rationale": "Same failure shape.",
                    "citations": ["INC-1042"],
                    "would_falsify": "Pool usage well under its limit.",
                },
                {
                    "action": "Look at deploy history for unrelated config drift.",
                    "rationale": "General hygiene.",
                    "citations": ["RB-04"],
                },
            ]
        )
    ]

    result = await service.investigate(strong)

    assert [s.citations for s in result.steps] == [("INC-1042",)]
    assert any("cites no historical case" in n for n in result.validation_notes)


async def test_uncited_recommendations_collapse_into_an_escalation(
    service, provider, strong
):
    """If nothing survives citation checking, even after one retry, there is nothing to propose."""
    uncited = draft(
        steps=[
            {
                "action": "Roll back immediately.",
                "rationale": "Trust me.",
                "citations": ["INC-0000"],
            }
        ]
    )
    provider.responses = [uncited, uncited]

    result = await service.investigate(strong)

    assert result.mode is ResponseMode.ESCALATION
    assert result.root_cause_status is RootCauseStatus.UNDETERMINED
    assert result.root_cause_hypothesis is None
    assert result.escalation is not None
    assert any("No recommended step survived" in n for n in result.validation_notes)
    assert "escalation stands" in result.validation_notes[-1]
    assert len(provider.prompts) == 2


async def test_the_schema_only_lets_a_step_cite_retrieved_cases(retriever, strong):
    from app.services.prompts import response_schema

    pack = await retriever.build_evidence(strong)
    step = response_schema(pack)["properties"]["steps"]["items"]
    cases = step["properties"]["cases"]

    assert "cases" in step["required"]
    assert cases["minItems"] == 1
    assert "INC-1042" in cases["items"]["enum"]
    assert not any(i.startswith("RB-") for i in cases["items"]["enum"])
    assert all(i.startswith("RB-") for i in step["properties"]["runbooks"]["items"]["enum"])


async def test_a_draft_that_loses_every_step_gets_one_retry_with_the_reasons(
    service, provider, strong
):
    provider.responses = [
        draft(
            steps=[
                {
                    "action": "Review client-side pool metrics.",
                    "rationale": "Runbook says so.",
                    "citations": ["RB-17"],
                }
            ]
        ),
        draft(
            steps=[
                {
                    "action": "Compare Redis connection pool usage against INC-1042.",
                    "rationale": "Same failure shape.",
                    "citations": ["INC-1042"],
                    "would_falsify": "Pool usage well under its limit.",
                }
            ]
        ),
    ]

    result = await service.investigate(strong)

    assert result.mode is ResponseMode.INVESTIGATION
    assert [s.citations for s in result.steps] == [("INC-1042",)]
    assert result.validation_notes[0].startswith("Every step in the first draft was dropped")
    retry_prompt = provider.prompts[1][-1].content
    assert "cites no historical case" in retry_prompt


async def test_weak_evidence_escalates_even_when_the_model_names_a_cause(
    service, provider, weak
):
    provider.responses = [
        draft(
            root_cause_status="hypothesis",
            root_cause_hypothesis="Definitely a database outage.",
            steps=[
                {
                    "action": "Check the database.",
                    "rationale": "It is always the database.",
                    "citations": ["RB-04"],
                }
            ],
        )
    ]

    result = await service.investigate(weak)

    assert result.mode is ResponseMode.ESCALATION
    assert result.root_cause_status is RootCauseStatus.UNDETERMINED
    assert result.root_cause_hypothesis is None
    assert "Definitely a database outage" not in result.render_text()
    assert any("did not support" in n for n in result.validation_notes)


async def test_a_stated_cause_labelled_undetermined_is_recorded_as_a_hypothesis(
    service, provider, strong
):
    provider.responses = [draft(root_cause_status="undetermined")]

    result = await service.investigate(strong)

    assert result.root_cause_status is RootCauseStatus.HYPOTHESIS
    assert any("labelled its own answer undetermined" in n for n in result.validation_notes)


async def test_no_stated_cause_on_strong_evidence_still_escalates(
    service, provider, strong
):
    provider.responses = [
        draft(root_cause_status="undetermined", root_cause_hypothesis="")
    ]

    result = await service.investigate(strong)

    assert result.mode is ResponseMode.ESCALATION
    assert any("none was stated" in n for n in result.validation_notes)


async def test_escalation_report_carries_every_required_field(service, provider, weak):
    provider.responses = [draft(steps=[], root_cause_hypothesis="")]

    report = (await service.investigate(weak)).escalation

    assert report is not None
    assert report.service == "reporting-api"
    assert report.severity == "SEV-3"
    assert report.environment == "production"
    assert report.observed_symptoms
    assert report.timeline == ("No timeline was supplied with this incident.",)
    assert report.historical_correlation
    assert report.evidence_gathered
    assert report.unresolved
    assert report.requested_team
    assert report.recommended_next_investigation


async def test_escalation_report_records_what_retrieval_could_not_do(
    service, provider, weak
):
    provider.responses = [draft(steps=[], root_cause_hypothesis="")]

    report = (await service.investigate(weak)).escalation

    assert any("not in the service map" in line for line in report.evidence_gathered)


async def test_escalation_report_falls_back_to_runbook_steps_for_next_checks(
    service, provider, weak
):
    provider.responses = [draft(steps=[], root_cause_hypothesis="")]

    report = (await service.investigate(weak)).escalation

    assert report.recommended_next_investigation
    assert "dashboard" in " ".join(report.recommended_next_investigation)


async def test_rendered_output_never_claims_a_fix_was_applied(
    service, provider, strong, weak
):
    provider.responses = [draft(), draft(steps=[], root_cause_hypothesis="")]

    investigation = (await service.investigate(strong)).render_text()
    escalation = (await service.investigate(weak)).render_text()

    assert "An engineer decides and acts." in investigation
    assert "No remediation has been applied." in escalation


async def test_unparseable_model_output_degrades_to_a_report(
    service, provider, strong
):
    provider.responses = ["this is not json"]

    result = await service.investigate(strong)

    assert result.mode is ResponseMode.ESCALATION
    assert result.escalation is not None
    assert any("did not parse" in n for n in result.validation_notes)
    assert result.headline


async def test_runtime_failure_degrades_to_a_report(service, provider, strong):
    async def explode(*_args, **_kwargs):
        from app.llm import LLMError

        raise LLMError("connection refused")

    provider.chat = explode

    result = await service.investigate(strong)

    assert result.mode is ResponseMode.ESCALATION
    assert any("did not respond" in n for n in result.validation_notes)
    assert result.escalation.historical_correlation


async def test_gate_unknowns_are_merged_into_the_result(service, provider, weak):
    provider.responses = [draft(unresolved=["Something the model noticed."], steps=[])]

    result = await service.investigate(weak)

    assert "Something the model noticed." in result.unresolved
    assert any("No event timeline" in u for u in result.unresolved)


async def test_the_conflict_is_stated_once_not_in_cautions_or_unresolved(
    service, provider, strong
):
    provider.responses = [
        draft(
            cautions=[
                "Historical guidance on the Redis pool size conflicts between cases.",
                "INC-0412 is stale.",
            ],
            unresolved=[
                "INC-1042 raised the pool and INC-1198 lowered it.",
                "Whether v2.8.1 changed the client library.",
            ],
        )
    ]

    result = await service.investigate(strong)

    assert result.cautions == ("INC-0412 is stale.",)
    assert result.unresolved == ("Whether v2.8.1 changed the client library.",)
    assert result.evidence.conflicts, "the conflict itself is still reported"
    assert any("shown once in the conflict panel" in n for n in result.validation_notes)


async def test_a_step_that_picks_a_side_of_the_conflict_is_dropped(
    service, provider, strong
):
    provider.responses = [
        draft(
            steps=[
                {
                    "action": "Increase the Redis connection pool size to 100.",
                    "rationale": "INC-1042 fixed it this way.",
                    "would_falsify": "",
                    "citations": ["INC-1042"],
                },
                {
                    "action": "Check pool checkout waits per replica before changing the pool size.",
                    "rationale": "Separates client pool exhaustion from Redis maxclients.",
                    "would_falsify": "No waiters on any replica.",
                    "citations": ["INC-1042", "INC-1198"],
                },
            ]
        )
    ]

    result = await service.investigate(strong)

    assert [s.action for s in result.steps] == [
        "Check pool checkout waits per replica before changing the pool size."
    ]
    assert any("disagree about" in n for n in result.validation_notes)


async def test_a_recorded_finding_allows_the_favoured_direction_only(
    service, provider, strong
):
    from dataclasses import replace

    from app.domain import Finding

    measured = replace(
        strong,
        findings=(
            Finding(
                target="redis_connection_pool_size",
                component="payment-api",
                favours_incident_id="INC-1042",
                observation="pool=50/50 per replica, Redis connected_clients 418/1500",
            ),
        ),
    )
    provider.responses = [
        draft(
            steps=[
                {
                    "action": "Decrease the Redis pool size to 30 on payment-api.",
                    "rationale": "INC-1198 cut the pool.",
                    "would_falsify": "",
                    "citations": ["INC-1198"],
                },
                {
                    "action": "Increase the Redis pool size on payment-api within the maxclients budget.",
                    "rationale": "INC-1042 raised the pool after client pool exhaustion.",
                    "would_falsify": "",
                    "citations": ["INC-1042"],
                },
            ]
        )
    ]

    result = await service.investigate(measured)

    assert result.confidence == pytest.approx(result.evidence.top.score)
    assert [s.citations for s in result.steps] == [("INC-1042",)]
    assert any("favours INC-1042" in n for n in result.validation_notes)


async def test_after_a_finding_the_ruled_out_side_cannot_carry_a_step(
    service, provider, strong
):
    """Seen with qwen2.5:7b: a paraphrased knob, cited only to the losing side."""
    from dataclasses import replace

    from app.domain import Finding

    measured = replace(
        strong,
        findings=(
            Finding(
                target="redis_connection_pool_size",
                component="payment-api",
                favours_incident_id="INC-1042",
                observation="Client pool 50/50 in use; connected_clients 418 of 1500.",
            ),
        ),
    )
    provider.responses = [
        draft(
            steps=[
                {
                    "action": "Reduce the oversized client pool",
                    "rationale": "If the issue is app-side exhaustion, a smaller pool resolves it.",
                    "would_falsify": "",
                    "citations": ["RB-17", "PM-1198"],
                },
                {
                    "action": "Read autoscaling history for replica changes.",
                    "rationale": "INC-1198 followed an autoscale.",
                    "would_falsify": "No replica change in the window.",
                    "citations": ["INC-1198"],
                },
                {
                    "action": "Compare Redis calls per request between v2.8.0 and v2.8.1.",
                    "rationale": "INC-1042 added Redis calls per request.",
                    "would_falsify": "Same number of Redis calls per request.",
                    "citations": ["INC-1042"],
                },
            ],
            cautions=[
                "Settled by an engineer's measurement in favour of INC-1042: "
                "client pool 50/50 in use.",
            ],
        )
    ]

    result = await service.investigate(measured)

    assert [s.citations for s in result.steps] == [("INC-1042",)]
    notes = " ".join(result.validation_notes)
    assert "the way INC-1198 did" in notes
    assert "rests only on INC-1198" in notes
    assert result.cautions == ()


def test_the_head_noun_of_a_knob_counts_as_a_mention():
    from app.analysis.text import mentions_knob

    assert mentions_knob("Reduce the oversized client pool", "redis_connection_pool_size")
    assert mentions_knob("raise the pool size", "redis_connection_pool_size")
    assert not mentions_knob("Redis latency is high", "redis_connection_pool_size")


async def test_a_citation_whose_document_does_not_say_it_is_dropped(
    service, provider, strong
):
    provider.responses = [
        draft(
            steps=[
                {
                    "action": "Read the client-side pool metrics per replica.",
                    "rationale": "Separates app-side exhaustion from Redis saturation.",
                    "would_falsify": "",
                    "citations": ["INC-1042", "INC-1210"],
                }
            ]
        )
    ]

    result = await service.investigate(strong)

    assert result.steps[0].citations == ("INC-1042",)
    assert result.steps[0].support and result.steps[0].support >= 0.2
    assert any(
        "INC-1210" in n and "does not mention" in n for n in result.validation_notes
    )


async def test_runbook_transcription_without_a_case_is_dropped(
    service, provider, strong, repo
):
    copied = repo.runbook("RB-17").steps[0]
    provider.responses = [
        draft(
            steps=[
                {
                    "action": copied,
                    "rationale": "From the runbook.",
                    "would_falsify": "",
                    "citations": ["RB-17"],
                },
                {
                    "action": "Read the client-side pool metrics per replica.",
                    "rationale": "Separates app-side exhaustion from Redis saturation.",
                    "would_falsify": "",
                    "citations": ["INC-1042"],
                },
            ]
        )
    ]

    result = await service.investigate(strong)

    assert [s.citations for s in result.steps] == [("INC-1042",)]
    assert any("restates RB-17" in n for n in result.validation_notes)


async def test_steps_are_capped_and_case_backed_steps_come_first(
    service, provider, strong, settings
):
    case_step = {
        "action": "Read the client-side pool metrics per replica.",
        "rationale": "Separates app-side exhaustion from Redis saturation.",
        "would_falsify": "",
        "citations": ["INC-1042"],
    }
    runbook_step = {
        "action": "Page the payments on-call with the Redis pool dashboard.",
        "rationale": "Redis dependency failure procedure.",
        "would_falsify": "",
        "citations": ["RB-17"],
    }
    provider.responses = [draft(steps=[runbook_step] + [case_step] * 6)]

    result = await service.investigate(strong)

    assert len(result.steps) == settings.max_steps
    assert all(s.citations == ("INC-1042",) for s in result.steps)
    assert any("Kept the first" in n for n in result.validation_notes)


async def test_a_hypothesis_is_attributed_to_the_case_it_rests_on(
    service, provider, strong
):
    provider.responses = [
        draft(root_cause_hypothesis="Redis client connection pool exhausted after the deploy.")
    ]

    result = await service.investigate(strong)

    assert result.root_cause_hypothesis.endswith("(as recorded in INC-1042).")
    assert result.hypothesis_basis[0] == "INC-1042"


async def test_an_ungrounded_hypothesis_is_removed_and_the_result_escalates(
    service, provider, strong
):
    provider.responses = [
        draft(root_cause_hypothesis="A kernel bug in the load balancer firmware.")
    ]

    result = await service.investigate(strong)

    assert result.mode is ResponseMode.ESCALATION
    assert result.root_cause_hypothesis is None
    assert any("no footing" in n for n in result.validation_notes)


async def test_a_remediation_inside_the_hypothesis_is_cut(service, provider, strong):
    provider.responses = [
        draft(
            root_cause_hypothesis=(
                "Redis client pool exhaustion after v2.8.1, as in INC-1042. "
                "Increase the Redis pool size to 100 to fix it."
            )
        )
    ]

    result = await service.investigate(strong)

    assert result.root_cause_hypothesis == (
        "Redis client pool exhaustion after v2.8.1, as in INC-1042."
    )
    assert any("Removed a remediation" in n for n in result.validation_notes)


async def test_prompt_contains_only_retrieved_evidence(service, provider, strong):
    provider.responses = [draft()]

    await service.investigate(strong)
    prompt = provider.prompts[0][1].content

    assert "ALLOWED CITATIONS" in prompt
    assert "INC-1042" in prompt
    # The staging distractor was filtered, so the model must not see it as
    # citable evidence.
    assert "INC-0995 (staging incident" in prompt
    assert "Deliberately excluded" in prompt


def test_title_style_headlines_are_recognised():
    from app.services.investigation import _looks_like_title

    assert _looks_like_title(
        "Incident Analysis and Resolution for Payment API Redis Dependency Failure"
    )
    assert _looks_like_title("Payment API Incident Analysis and Resolution")
    assert not _looks_like_title(
        "payment-api is running out of Redis connections after v2.8.1, as in INC-1042."
    )
