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
        "root_cause_hypothesis": "Redis client pool exhaustion after v2.8.1.",
        "steps": [
            {
                "action": "Read the client-side pool metrics per replica.",
                "rationale": "Separates app-side exhaustion from dependency saturation.",
                "citations": ["INC-1042", "RB-17"],
            }
        ],
        "cautions": ["Historical guidance on pool size conflicts."],
        "unresolved": ["Aggregate pool utilisation is unmeasured."],
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
                    "action": "Read the pool metrics.",
                    "rationale": "Cited.",
                    "citations": ["RB-17"],
                },
            ]
        )
    ]

    result = await service.investigate(strong)

    assert [s.action for s in result.steps] == ["Read the pool metrics."]
    assert any("Restart everything" in n for n in result.validation_notes)


async def test_uncited_recommendations_collapse_into_an_escalation(
    service, provider, strong
):
    """If nothing survives citation checking there is nothing to propose."""
    provider.responses = [
        draft(
            steps=[
                {
                    "action": "Roll back immediately.",
                    "rationale": "Trust me.",
                    "citations": ["INC-0000"],
                }
            ]
        )
    ]

    result = await service.investigate(strong)

    assert result.mode is ResponseMode.ESCALATION
    assert result.root_cause_status is RootCauseStatus.UNDETERMINED
    assert result.root_cause_hypothesis is None
    assert result.escalation is not None
    assert any("No recommended step survived" in n for n in result.validation_notes)


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


async def test_gate_unknowns_are_merged_into_the_result(service, provider, strong):
    provider.responses = [draft(unresolved=["Something the model noticed."])]

    result = await service.investigate(strong)

    assert "Something the model noticed." in result.unresolved
    assert any("conflicts" in u for u in result.unresolved)


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
