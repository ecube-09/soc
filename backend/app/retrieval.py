"""Hybrid retrieval.

Two arms feed the candidate set. The semantic arm finds cases that read like the
current incident. The metadata arm adds every case on the same service and its
one-hop neighbours, because the right case is sometimes phrased nothing like the
symptoms an engineer typed.

The union is then filtered on environment, scored on six components, and ranked.
Nothing here calls the chat model.
"""

from datetime import datetime

from app.analysis import conflicts as conflict_analysis
from app.analysis import gate as gate_analysis
from app.analysis import scoring, temporal
from app.analysis.evidence import (
    CandidateCase,
    EvidencePack,
    ExcludedCase,
    RunbookMatch,
)
from app.analysis.scoring import ServiceGraph
from app.config import Settings
from app.db.repository import SqliteRepository
from app.domain import CurrentIncident, Incident, Runbook
from app.llm import LLMProvider
from app.vectors import VectorStore


class HybridRetriever:
    def __init__(
        self,
        repo: SqliteRepository,
        store: VectorStore,
        provider: LLMProvider,
        settings: Settings,
        *,
        origin: str | None = None,
    ) -> None:
        self.repo = repo
        self.store = store
        self.provider = provider
        self.settings = settings
        # When set, only knowledge with this origin is eligible. The eval uses it
        # so imported outside text can never move a labelled score.
        self.origin = origin

    async def build_evidence(
        self, current: CurrentIncident, *, now: datetime | None = None
    ) -> EvidencePack:
        now = now or self._reference_now(current)
        graph = ServiceGraph.from_services(self.repo.services())
        incidents = [i for i in self.repo.incidents() if self._eligible(i.origin)]
        runbooks = [r for r in self.repo.runbooks() if self._eligible(r.origin)]

        semantic, runbook_similarity = await self._similarity(current)

        kept, excluded = self._filter_scope(
            current, self._candidate_incidents(current, incidents, semantic, graph)
        )
        candidates = self._score(current, kept, semantic, graph, now=now)
        candidates = candidates[: self.settings.max_comparable_cases]

        runbook_matches = self._rank_runbooks(
            current, runbooks, runbook_similarity, graph
        )
        # Conflicts are drawn from the cases that survived filtering. A staging
        # config change is not conflicting guidance for a production incident.
        found_conflicts = conflict_analysis.conflicts_for(
            kept,
            {c.incident.incident_id for c in candidates},
            {current.service, *current.suspected_dependencies},
        )

        decision = gate_analysis.evaluate(
            has_timeline=bool(current.events),
            candidates=candidates,
            runbooks=runbook_matches,
            conflicts=found_conflicts,
            settings=self.settings,
            findings=current.findings,
        )

        notes: list[str] = list(decision.notes)
        if not graph.knows(current.service):
            notes.append(
                f"{current.service} is not in the service map, so dependency-based "
                "relevance could not be used."
            )

        return EvidencePack(
            current=current,
            candidates=tuple(candidates),
            runbooks=tuple(runbook_matches),
            conflicts=tuple(found_conflicts),
            excluded=tuple(excluded),
            unknowns=decision.unknowns,
            mode=decision.mode,
            confidence=decision.confidence,
            gate_reason=decision.reason,
            escalate_to=decision.escalate_to,
            notes=tuple(notes),
            support=decision.support,
            resolutions=decision.resolutions,
        )

    def _eligible(self, origin: str) -> bool:
        return self.origin is None or origin == self.origin

    async def _similarity(
        self, current: CurrentIncident
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Best chunk similarity per source id, keyed by INC/PM/RB/LOG id.

        Runbooks get their own query. A single ranked list is dominated by
        incident and postmortem chunks, which are far more numerous, so the
        procedure that actually applies can fall off the end of it.
        """
        embedding = (await self.provider.embed([query_text(current)]))[0]

        return (
            self._best_per_source(
                self.store.query(embedding, limit=self.settings.semantic_candidates)
            ),
            self._best_per_source(
                self.store.query(
                    embedding,
                    limit=self.settings.runbook_candidates,
                    where={"source_type": "runbook"},
                )
            ),
        )

    @staticmethod
    def _best_per_source(hits) -> dict[str, float]:
        best: dict[str, float] = {}
        for hit in hits:
            source_id = str(hit.metadata.get("source_id") or hit.document_id)
            best[source_id] = max(best.get(source_id, 0.0), hit.similarity)
        return best

    def _candidate_incidents(
        self,
        current: CurrentIncident,
        incidents: list[Incident],
        semantic: dict[str, float],
        graph: ServiceGraph,
    ) -> list[Incident]:
        by_id = {incident.incident_id: incident for incident in incidents}
        postmortem_owner = {
            p.postmortem_id: p.incident_id for p in self.repo.postmortems()
        }

        selected: dict[str, Incident] = {}

        # Semantic arm: a hit on a postmortem or a log is a hit on its incident.
        for source_id in semantic:
            incident_id = postmortem_owner.get(source_id, source_id)
            if incident_id in by_id:
                selected[incident_id] = by_id[incident_id]
            else:
                log = self.repo.log_excerpt(source_id)
                if log and log.incident_id in by_id:
                    selected[log.incident_id] = by_id[log.incident_id]

        # Metadata arm: the current service and its one-hop neighbourhood, plus
        # anything the reporter already suspects.
        neighbourhood = graph.neighbourhood(current.service) | set(
            current.suspected_dependencies
        )
        for incident in incidents:
            if incident.service in neighbourhood or (
                set(incident.related_services) & neighbourhood
            ):
                selected[incident.incident_id] = incident

        return list(selected.values())

    def _filter_scope(
        self, current: CurrentIncident, incidents: list[Incident]
    ) -> tuple[list[Incident], list[ExcludedCase]]:
        """A staging case is the wrong case, however well the words line up.

        Region is a hard filter only when the policy says so. Either way the
        decision is reported, never silent.
        """
        kept: list[Incident] = []
        excluded: list[ExcludedCase] = []
        for incident in incidents:
            if incident.environment != current.environment:
                excluded.append(
                    ExcludedCase(
                        incident_id=incident.incident_id,
                        title=incident.title,
                        reason=(
                            f"{incident.environment.value} incident, current is "
                            f"{current.environment.value}"
                        ),
                        filter="environment",
                    )
                )
            elif self.settings.region_policy == "require" and scoring.region_differs(
                current, incident
            ):
                excluded.append(
                    ExcludedCase(
                        incident_id=incident.incident_id,
                        title=incident.title,
                        reason=(
                            f"{incident.region} incident, current is {current.region} "
                            "(region policy: require)"
                        ),
                        filter="region",
                    )
                )
            else:
                kept.append(incident)
        return kept, excluded

    def _score(
        self,
        current: CurrentIncident,
        incidents: list[Incident],
        semantic: dict[str, float],
        graph: ServiceGraph,
        *,
        now: datetime,
    ) -> list[CandidateCase]:
        settings = self.settings
        scored: list[CandidateCase] = []

        for incident in incidents:
            similarity = semantic.get(incident.incident_id, 0.0)
            metadata = scoring.metadata_score(current, incident)
            alignment = temporal.align(current.events, incident.events)
            recency = scoring.recency_score(
                incident, now=now, half_life_days=settings.recency_half_life_days
            )
            graph_score, graph_reason = scoring.service_graph_score(
                current, incident, graph
            )
            stale = scoring.is_stale(
                incident, now=now, stale_after_days=settings.stale_after_days
            )

            total = (
                settings.weight_semantic * similarity
                + settings.weight_metadata * metadata.score
                + settings.weight_timeline_sequence * alignment.sequence_score
                + settings.weight_timeline_interval * alignment.interval_score
                + settings.weight_recency * recency
                + settings.weight_service_graph * graph_score
            )

            flags: list[str] = []
            if stale:
                flags.append("stale")
            if incident.service == current.service:
                flags.append("same-service")
            elif graph_score >= 0.5:
                flags.append("dependency")
            if alignment.matched and alignment.sequence_score >= 0.7:
                flags.append("timeline-match")
            if scoring.region_differs(current, incident):
                flags.append("other-region")

            scored.append(
                CandidateCase(
                    incident=incident,
                    semantic=similarity,
                    metadata_score=metadata.score,
                    metadata_reasons=metadata.reasons,
                    timeline=alignment,
                    recency=recency,
                    service_graph=graph_score,
                    graph_reason=graph_reason,
                    score=total,
                    age_days=scoring.age_days(incident, now=now),
                    flags=tuple(flags),
                    postmortem=self.repo.postmortem_for(incident.incident_id),
                    logs=tuple(self.repo.log_excerpts_for(incident.incident_id)),
                )
            )

        scored.sort(key=lambda c: (c.score, c.incident.started_at), reverse=True)
        return scored

    def _rank_runbooks(
        self,
        current: CurrentIncident,
        runbooks: list[Runbook],
        semantic: dict[str, float],
        graph: ServiceGraph,
    ) -> list[RunbookMatch]:
        matches: list[RunbookMatch] = []
        neighbourhood = graph.neighbourhood(current.service) | set(
            current.suspected_dependencies
        )

        settings = self.settings
        for runbook in runbooks:
            similarity = semantic.get(runbook.runbook_id, 0.0)
            coverage = scoring.trigger_coverage(current, runbook)

            if runbook.service == current.service:
                applicability, reason = 1.0, f"scoped to {current.service}"
            elif runbook.service is None:
                applicability, reason = 0.7, "applies to any service"
            elif runbook.service in neighbourhood:
                applicability, reason = 0.6, f"scoped to {runbook.service}, a dependency"
            else:
                applicability, reason = 0.0, f"scoped to {runbook.service}"

            if applicability == 0.0 and similarity < 0.5:
                continue

            if coverage >= 0.4:
                reason = f"trigger matches the reported symptoms; {reason}"

            matches.append(
                RunbookMatch(
                    runbook=runbook,
                    semantic=similarity,
                    score=(
                        settings.runbook_weight_semantic * similarity
                        + settings.runbook_weight_trigger * coverage
                        + settings.runbook_weight_applicability * applicability
                    ),
                    reason=reason,
                )
            )

        matches.sort(key=lambda m: m.score, reverse=True)
        return matches[: self.settings.max_runbooks]

    @staticmethod
    def _reference_now(current: CurrentIncident) -> datetime:
        """Age is measured against the incident, not the wall clock.

        A case reopened a year later should not silently change its rankings.
        """
        return current.anchor_time or datetime.now()


def query_text(current: CurrentIncident) -> str:
    """What gets embedded: the symptoms and the shape, not the prose alone."""
    parts = [
        current.title,
        current.description,
        f"service: {current.service}",
        f"environment: {current.environment.value}",
        f"severity: {current.severity.value}",
    ]
    if current.symptoms:
        parts.append("symptoms: " + "; ".join(current.symptoms))
    if current.suspected_dependencies:
        parts.append("dependencies: " + ", ".join(current.suspected_dependencies))
    if current.events:
        anchor, normalized = temporal.normalize(current.events)
        shape = " -> ".join(
            f"{e.type.value}+{e.offset_minutes}m" for e in normalized
        )
        parts.append(f"timeline anchored on {anchor}: {shape}")
    if current.tech_context:
        parts.append("stack: " + ", ".join(current.tech_context))
    if current.notes:
        parts.append("engineer notes: " + "; ".join(current.notes))
    if current.log_excerpt:
        parts.append("logs:\n" + current.log_excerpt)
    return "\n".join(parts)
