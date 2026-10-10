"""How much do the hand-tuned numbers matter?

The six weights, the 0.45 threshold, the 120-day half-life, and the 540-day
stale cutoff were set so this corpus behaves. That makes the eval's 100% a
statement about the pipeline, not about the numbers. This nudges each one, re-runs
retrieval and the gate on every labelled case, and reports whether the top match
or the response mode flips. A setting where a small nudge flips an answer is one
where the eval score is resting on a knife edge.

Weights are not renormalised after a nudge, on purpose: the total moves against
the threshold, which is part of what is being tested.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.config import Settings
from app.corpus import EvalCase
from app.db.repository import SqliteRepository
from app.eval import WEIGHT_FIELDS, CaseScore, _passed, score_case
from app.llm import LLMProvider
from app.retrieval import HybridRetriever
from app.vectors import VectorStore

@dataclass(frozen=True)
class Perturbation:
    label: str
    field: str
    apply: Callable[[float], float]


def default_perturbations(step: float = 0.25) -> tuple[Perturbation, ...]:
    out: list[Perturbation] = []
    for name in WEIGHT_FIELDS:
        short = name.removeprefix("weight_")
        out.append(Perturbation(f"{short} -{step:.0%}", name, lambda v: v * (1 - step)))
        out.append(Perturbation(f"{short} +{step:.0%}", name, lambda v: v * (1 + step)))
    out += [
        Perturbation("threshold -0.05", "confidence_threshold", lambda v: v - 0.05),
        Perturbation("threshold +0.05", "confidence_threshold", lambda v: v + 0.05),
        Perturbation("half-life x0.5", "recency_half_life_days", lambda v: v * 0.5),
        Perturbation("half-life x2", "recency_half_life_days", lambda v: v * 2),
        Perturbation("stale cutoff -25%", "stale_after_days", lambda v: int(v * 0.75)),
        Perturbation("stale cutoff +25%", "stale_after_days", lambda v: int(v * 1.25)),
        Perturbation("conflict penalty x0.5", "conflict_confidence_penalty", lambda v: v * 0.5),
        Perturbation("conflict penalty x2", "conflict_confidence_penalty", lambda v: v * 2),
    ]
    return tuple(out)


@dataclass
class Row:
    label: str
    top1_flips: list[str] = field(default_factory=list)
    mode_flips: list[str] = field(default_factory=list)
    failing: list[str] = field(default_factory=list)

    @property
    def stable(self) -> bool:
        return not (self.top1_flips or self.mode_flips or self.failing)

    def to_dict(self) -> dict[str, object]:
        return {
            "perturbation": self.label,
            "top1_flips": self.top1_flips,
            "mode_flips": self.mode_flips,
            "failing": self.failing,
            "stable": self.stable,
        }


@dataclass
class Report:
    baseline: list[CaseScore]
    margins: dict[str, float | None]
    rows: list[Row]

    def to_dict(self) -> dict[str, object]:
        return {
            "margins": self.margins,
            "rows": [r.to_dict() for r in self.rows],
            "stable": sum(r.stable for r in self.rows),
            "total": len(self.rows),
        }


class CachingProvider:
    """Embeds each query once; every perturbation reuses the same vectors."""

    def __init__(self, inner: LLMProvider) -> None:
        self.inner = inner
        self.chat_model = inner.chat_model
        self.embedding_model = inner.embedding_model
        self._cache: dict[str, list[float]] = {}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        missing = [t for t in texts if t not in self._cache]
        if missing:
            for text, vector in zip(missing, await self.inner.embed(missing), strict=True):
                self._cache[text] = vector
        return [self._cache[t] for t in texts]

    async def chat(self, *args, **kwargs):
        return await self.inner.chat(*args, **kwargs)

    async def status(self):
        return await self.inner.status()

    async def aclose(self) -> None:
        return None


async def run(
    *,
    repo: SqliteRepository,
    store: VectorStore,
    provider: LLMProvider,
    settings: Settings,
    cases: Sequence[EvalCase],
    origin: str | None,
    perturbations: Sequence[Perturbation] | None = None,
) -> Report:
    cached = CachingProvider(provider)

    async def score_all(active: Settings) -> list[tuple[CaseScore, list[float]]]:
        retriever = HybridRetriever(repo, store, cached, active, origin=origin)
        out = []
        for case in cases:
            pack = await retriever.build_evidence(case.incident)
            out.append((score_case(case, pack), [c.score for c in pack.candidates]))
        return out

    baseline = await score_all(settings)
    margins = {
        score.case_id: round(scores[0] - scores[1], 3) if len(scores) > 1 else None
        for score, scores in baseline
    }

    rows: list[Row] = []
    for perturbation in perturbations or default_perturbations():
        nudged = settings.model_copy(
            update={
                perturbation.field: perturbation.apply(getattr(settings, perturbation.field))
            }
        )
        row = Row(perturbation.label)
        for (base, _), (now, _) in zip(baseline, await score_all(nudged), strict=True):
            if base.top_incident != now.top_incident:
                row.top1_flips.append(f"{now.case_id} {base.top_incident}->{now.top_incident}")
            if base.actual_mode != now.actual_mode:
                row.mode_flips.append(f"{now.case_id} {base.actual_mode}->{now.actual_mode}")
            if not _passed(now):
                row.failing.append(now.case_id)
        rows.append(row)

    return Report(baseline=[s for s, _ in baseline], margins=margins, rows=rows)


def print_report(report: Report) -> None:
    print("Sensitivity: each setting nudged on its own, all labelled cases re-run\n")
    print("Top-1 margin at baseline (score gap between first and second case):")
    for case_id, margin in report.margins.items():
        print(f"  {case_id}  {'n/a' if margin is None else f'{margin:.3f}'}")
    print()
    width = max(len(r.label) for r in report.rows)
    for row in report.rows:
        if row.stable:
            detail = "stable"
        else:
            parts = []
            if row.top1_flips:
                parts.append("top-1 " + "; ".join(row.top1_flips))
            if row.mode_flips:
                parts.append("mode " + "; ".join(row.mode_flips))
            if row.failing:
                parts.append("fails " + ", ".join(row.failing))
            detail = " | ".join(parts)
        print(f"  {row.label.ljust(width)}  {detail}")
    stable = sum(r.stable for r in report.rows)
    harmless = sum(not r.stable and not r.mode_flips and not r.failing for r in report.rows)
    print(f"\n{stable}/{len(report.rows)} perturbations leave every labelled outcome unchanged.")
    if harmless:
        print(
            f"{harmless} more move only the closest case without changing the mode or "
            "failing a label (typically an escalation case, where the closest case "
            "is not asserted)."
        )
    fragile = [case_id for case_id, m in report.margins.items() if m is not None and m < 0.02]
    if fragile:
        print(
            f"Margins under 0.02 ({', '.join(fragile)}) mean the order of the top two "
            "is a coin toss under these weights."
        )
