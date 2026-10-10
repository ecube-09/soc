"""Scored evaluation against the labelled cases.

Retrieval quality is the thing this project actually rises or falls on, so it is
measured rather than eyeballed. Two modes:

- retrieval only, which needs the embedding model and is fast and deterministic;
- with generation, which additionally checks that every citation the model
  produced points at evidence that was really retrieved.
"""

import json
from dataclasses import dataclass, field

from app.config import Settings, get_settings
from app.corpus import EvalCase, load_eval_cases
from app.db import SqliteRepository
from app.domain import ResponseMode
from app.llm import build_provider
from app.retrieval import HybridRetriever
from app.vectors import ChromaVectorStore


@dataclass
class CaseScore:
    case_id: str
    expected_mode: str
    actual_mode: str
    confidence: float
    top_incident: str | None
    recall_at_5: float | None
    top1_correct: bool | None
    stale_flagged: bool | None
    excluded_respected: bool | None
    runbook_at_1: bool | None
    conflicts_correct: bool
    citations_valid: bool | None = None
    invalid_citations: list[str] = field(default_factory=list)
    steps: int | None = None

    @property
    def mode_correct(self) -> bool:
        return self.expected_mode == self.actual_mode

    def to_dict(self) -> dict[str, object]:
        return {k: v for k, v in self.__dict__.items()} | {
            "mode_correct": self.mode_correct
        }


def score_case(case: EvalCase, pack) -> CaseScore:
    expected = case.expected
    retrieved = [c.incident.incident_id for c in pack.candidates]
    retrieved_set = set(retrieved)

    recall = None
    top1 = None
    if expected.incident_ids:
        found = retrieved_set & set(expected.incident_ids)
        recall = len(found) / len(expected.incident_ids)
        top1 = bool(retrieved) and retrieved[0] == expected.incident_ids[0]

    stale = None
    if expected.stale_ids:
        flagged = {
            c.incident.incident_id for c in pack.candidates if c.is_stale
        }
        stale = set(expected.stale_ids) <= flagged

    excluded = None
    if expected.excluded_ids:
        excluded = not (retrieved_set & set(expected.excluded_ids))

    runbook = None
    if expected.runbook_ids:
        runbook = (
            bool(pack.runbooks)
            and pack.runbooks[0].runbook.runbook_id == expected.runbook_ids[0]
        )

    return CaseScore(
        case_id=case.case_id,
        expected_mode=expected.mode,
        actual_mode=pack.mode.value,
        confidence=round(pack.confidence, 3),
        top_incident=retrieved[0] if retrieved else None,
        recall_at_5=recall,
        top1_correct=top1,
        stale_flagged=stale,
        excluded_respected=excluded,
        runbook_at_1=runbook,
        conflicts_correct={c.target for c in pack.conflicts}
        == set(expected.conflict_targets),
    )


def add_generation_score(score: CaseScore, pack, result) -> CaseScore:
    """Citation validity: does every cited id exist in the evidence pack?"""
    allowed = pack.citation_ids()
    cited = [c for step in result.steps for c in step.citations]
    invalid = sorted({c for c in cited if c not in allowed})

    score.actual_mode = result.mode.value
    score.citations_valid = not invalid
    score.invalid_citations = invalid
    score.steps = len(result.steps)
    return score


def calibration(settings: Settings, repo: SqliteRepository) -> dict[str, object]:
    """What the reported numbers rest on, stated next to them."""
    origins = repo.origins()
    return {
        "hand_tuned": True,
        "note": (
            "Weights, threshold, half-life, and stale cutoff were set by hand so this "
            "synthetic corpus behaves. These results show the pipeline does what it was "
            "built to do on the labelled cases; they are not an accuracy estimate for "
            "incidents the corpus was not written around. Run with --sensitivity to see "
            "how close each outcome is to flipping."
        ),
        "weights": {name: getattr(settings, name) for name in WEIGHT_FIELDS},
        "confidence_threshold": settings.confidence_threshold,
        "recency_half_life_days": settings.recency_half_life_days,
        "stale_after_days": settings.stale_after_days,
        "conflict_confidence_penalty": settings.conflict_confidence_penalty,
        "region_policy": settings.region_policy,
        "embedding_model": settings.embedding_model,
        "chat_model": settings.chat_model,
        "eval_origin": settings.eval_origin,
        "ignored_incidents_by_origin": {
            origin: n for origin, n in origins.items() if origin != settings.eval_origin
        },
    }


WEIGHT_FIELDS = (
    "weight_semantic",
    "weight_metadata",
    "weight_timeline_sequence",
    "weight_timeline_interval",
    "weight_recency",
    "weight_service_graph",
)


async def run_eval(
    *,
    as_json: bool = False,
    with_generation: bool = False,
    sensitivity: bool = False,
) -> int:
    settings = get_settings()
    repo = SqliteRepository(settings.sqlite_abs_path)
    provider = build_provider(settings)
    store = ChromaVectorStore(settings.chroma_abs_path, settings.chroma_collection)

    if store.count() == 0:
        print("Nothing is indexed. Run `python -m app.cli seed` then `ingest`.")
        return 1

    scores: list[CaseScore] = []
    report = None
    try:
        context = calibration(settings, repo)
        retriever = HybridRetriever(
            repo, store, provider, settings, origin=settings.eval_origin
        )
        service = None
        if with_generation:
            from app.services import InvestigationService

            service = InvestigationService(retriever, provider, repo, settings)

        cases = load_eval_cases()
        for case in cases:
            pack = await retriever.build_evidence(case.incident)
            score = score_case(case, pack)
            if service is not None:
                score = add_generation_score(
                    score, pack, await service.explain(pack)
                )
            scores.append(score)

        if sensitivity:
            from app import sensitivity as sensitivity_check

            report = await sensitivity_check.run(
                repo=repo,
                store=store,
                provider=provider,
                settings=settings,
                cases=cases,
                origin=settings.eval_origin,
            )
    finally:
        await provider.aclose()
        repo.close()

    if as_json:
        print(
            json.dumps(
                {
                    "calibration": context,
                    "cases": [s.to_dict() for s in scores],
                    "sensitivity": report.to_dict() if report else None,
                },
                indent=2,
            )
        )
    else:
        _print_calibration(context)
        _print_report(scores, with_generation=with_generation)
        if report:
            from app import sensitivity as sensitivity_check

            print()
            sensitivity_check.print_report(report)

    failures = [s for s in scores if not _passed(s)]
    return 1 if failures else 0


def _print_calibration(context: dict[str, object]) -> None:
    print("Read this first")
    print(f"  {context['note']}")
    weights = context["weights"]
    print(
        "  weights: "
        + ", ".join(f"{k.removeprefix('weight_')} {v}" for k, v in weights.items())
    )
    print(
        f"  threshold {context['confidence_threshold']}, half-life "
        f"{context['recency_half_life_days']}d, stale after {context['stale_after_days']}d, "
        f"region policy {context['region_policy']}"
    )
    print(
        f"  models: embeddings {context['embedding_model']}, chat {context['chat_model']}. "
        "Re-run with --with-generation if either changes; the hashing embedder used "
        "in tests does not prove a new embedding model keeps this ranking."
    )
    ignored = context["ignored_incidents_by_origin"]
    if ignored:
        print(
            f"  scored against origin={context['eval_origin']} only; ignored "
            + ", ".join(f"{n} {origin}" for origin, n in ignored.items())
        )
    print()


def _passed(score: CaseScore) -> bool:
    checks = [
        score.mode_correct,
        score.conflicts_correct,
        score.top1_correct,
        score.stale_flagged,
        score.excluded_respected,
        score.runbook_at_1,
        score.citations_valid,
    ]
    return all(check for check in checks if check is not None)


def _print_report(scores: list[CaseScore], *, with_generation: bool) -> None:
    print(f"{len(scores)} labelled cases\n")

    for score in scores:
        mark = "pass" if _passed(score) else "FAIL"
        print(f"[{mark}] {score.case_id}")
        print(
            f"       mode {score.actual_mode} (expected {score.expected_mode})"
            f"  confidence {score.confidence:.2f}"
        )
        if score.recall_at_5 is not None:
            print(
                f"       recall@5 {score.recall_at_5:.0%}"
                f"  top-1 {_yn(score.top1_correct)} ({score.top_incident})"
            )
        if score.runbook_at_1 is not None:
            print(f"       runbook@1 {_yn(score.runbook_at_1)}")
        if score.stale_flagged is not None:
            print(f"       stale case flagged {_yn(score.stale_flagged)}")
        if score.excluded_respected is not None:
            print(f"       wrong-environment case excluded {_yn(score.excluded_respected)}")
        print(f"       conflicts identified {_yn(score.conflicts_correct)}")
        if score.citations_valid is not None:
            print(
                f"       citations valid {_yn(score.citations_valid)}"
                f"  ({score.steps} step(s))"
                + (
                    f"  invalid: {', '.join(score.invalid_citations)}"
                    if score.invalid_citations
                    else ""
                )
            )
        print()

    print("Aggregate")
    print(f"  mode correct          {_rate(s.mode_correct for s in scores)}")
    print(f"  conflicts identified  {_rate(s.conflicts_correct for s in scores)}")
    print(f"  mean recall@5         {_mean(s.recall_at_5 for s in scores)}")
    print(f"  top-1 correct         {_rate_opt(s.top1_correct for s in scores)}")
    print(f"  runbook@1 correct     {_rate_opt(s.runbook_at_1 for s in scores)}")
    print(f"  stale flagged         {_rate_opt(s.stale_flagged for s in scores)}")
    print(f"  wrong env excluded    {_rate_opt(s.excluded_respected for s in scores)}")
    if with_generation:
        print(f"  citations valid       {_rate_opt(s.citations_valid for s in scores)}")

    failed = [s.case_id for s in scores if not _passed(s)]
    print()
    print("All labelled expectations met." if not failed else f"Failing: {', '.join(failed)}")


def _yn(value: bool | None) -> str:
    return "n/a" if value is None else ("yes" if value else "NO")


def _rate(values) -> str:
    items = list(values)
    return f"{sum(items)}/{len(items)}"


def _rate_opt(values) -> str:
    items = [v for v in values if v is not None]
    return f"{sum(items)}/{len(items)}" if items else "n/a"


def _mean(values) -> str:
    items = [v for v in values if v is not None]
    return f"{sum(items) / len(items):.0%}" if items else "n/a"
