"""The eval harness must fail when the system is wrong, so it is tested too."""

import dataclasses
import json

import pytest

from app.corpus import ExpectedOutcome
from app.eval import _passed, add_generation_score, score_case
from app.services import InvestigationService

DRAFT = json.dumps(
    {
        "headline": "Looks like INC-1042.",
        "root_cause_status": "hypothesis",
        "root_cause_hypothesis": "Redis client pool exhaustion.",
        "steps": [
            {
                "action": "Read the pool metrics.",
                "rationale": "Separates the two failure modes.",
                "citations": ["INC-1042", "INC-4242"],
            }
        ],
        "cautions": [],
        "unresolved": [],
        "requested_team": "payments",
    }
)


@pytest.fixture
async def scored(retriever, eval_cases):
    case = eval_cases["CUR-2051"]
    return case, score_case(case, await retriever.build_evidence(case.incident))


async def test_labelled_case_scores_a_pass(scored):
    _, score = scored

    assert score.mode_correct
    assert score.recall_at_5 == 1.0
    assert score.top1_correct
    assert score.stale_flagged
    assert score.excluded_respected
    assert score.runbook_at_1
    assert score.conflicts_correct
    assert _passed(score)


async def test_wrong_expected_incident_fails(retriever, eval_cases):
    case = eval_cases["CUR-2051"]
    mislabelled = dataclasses.replace(
        case,
        expected=dataclasses.replace(
            case.expected, incident_ids=("INC-1150",), stale_ids=(), runbook_ids=()
        ),
    )

    score = score_case(mislabelled, await retriever.build_evidence(case.incident))

    assert score.recall_at_5 == 0.0
    assert score.top1_correct is False
    assert not _passed(score)


async def test_wrong_expected_mode_fails(retriever, eval_cases):
    case = eval_cases["CUR-2051"]
    mislabelled = dataclasses.replace(
        case, expected=ExpectedOutcome("escalation", (), (), (), (), ())
    )

    score = score_case(mislabelled, await retriever.build_evidence(case.incident))

    assert not score.mode_correct
    assert not _passed(score)


async def test_missing_conflict_expectation_fails(retriever, eval_cases):
    case = eval_cases["CUR-2051"]
    mislabelled = dataclasses.replace(
        case, expected=dataclasses.replace(case.expected, conflict_targets=())
    )

    score = score_case(mislabelled, await retriever.build_evidence(case.incident))

    assert not score.conflicts_correct
    assert not _passed(score)


async def test_weak_case_needs_no_retrieval_metrics(retriever, eval_cases):
    case = eval_cases["CUR-2052"]

    score = score_case(case, await retriever.build_evidence(case.incident))

    assert score.actual_mode == "escalation"
    assert score.recall_at_5 is None
    assert score.top1_correct is None
    assert _passed(score)


def test_calibration_says_the_numbers_were_hand_tuned(settings, repo):
    from app.eval import calibration

    context = calibration(settings, repo)

    assert context["hand_tuned"] is True
    assert "set by hand" in context["note"]
    assert context["confidence_threshold"] == settings.confidence_threshold
    assert set(context["weights"]) >= {"weight_semantic", "weight_recency"}


async def test_sensitivity_reports_flips_per_perturbation(
    repo, settings, provider, eval_cases, retriever
):
    from app import sensitivity
    from app.sensitivity import Perturbation

    report = await sensitivity.run(
        repo=repo,
        store=retriever.store,
        provider=provider,
        settings=settings,
        cases=list(eval_cases.values()),
        origin="synthetic",
        perturbations=[
            Perturbation("no-op", "weight_semantic", lambda v: v),
            Perturbation("threshold to 0.95", "confidence_threshold", lambda v: 0.95),
        ],
    )

    noop, strict = report.rows
    assert noop.stable
    assert strict.mode_flips, "a 0.95 threshold must turn investigations into escalations"
    assert strict.failing
    assert set(report.margins) == set(eval_cases)


async def test_generation_scoring_counts_only_surviving_citations(
    retriever, repo, settings, provider, eval_cases
):
    """The invented id is dropped before scoring, so validity reflects the output."""
    case = eval_cases["CUR-2051"]
    pack = await retriever.build_evidence(case.incident)
    score = score_case(case, pack)

    provider.responses = [DRAFT]
    service = InvestigationService(retriever, provider, repo, settings)
    result = await service.explain(pack)

    score = add_generation_score(score, pack, result)

    assert score.citations_valid is True
    assert score.invalid_citations == []
    assert score.steps == 1
    assert "INC-4242" not in result.steps[0].citations
    assert _passed(score)
