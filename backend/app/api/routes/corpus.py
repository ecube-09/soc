"""Read-only endpoints the workspace needs: services, examples, and sources."""

from fastapi import APIRouter, HTTPException

from app.api.deps import RepositoryDep, SettingsDep, VectorStoreDep
from app.corpus import load_eval_cases
from app.domain import (
    RESERVED_ARTIFACT_KINDS,
    SUPPORTED_ARTIFACT_KINDS,
)
from app.schemas.investigation import (
    CorpusStatsOut,
    ExampleOut,
    ServiceOut,
    SourceOut,
)

router = APIRouter(tags=["corpus"])


@router.get("/services", response_model=list[ServiceOut])
async def list_services(repo: RepositoryDep) -> list[ServiceOut]:
    return [
        ServiceOut(
            name=service.name,
            team=service.team,
            tier=service.tier,
            description=service.description,
            dependencies=list(service.dependencies),
        )
        for service in repo.services()
    ]


@router.get("/examples", response_model=list[ExampleOut])
async def list_examples() -> list[ExampleOut]:
    """Labelled cases, offered as form prefills so the UI is usable cold."""
    return [
        ExampleOut.model_validate(
            {
                "case_id": case.case_id,
                "note": case.note,
                "incident": {
                    "title": case.incident.title,
                    "description": case.incident.description,
                    "service": case.incident.service,
                    "environment": case.incident.environment.value,
                    "severity": case.incident.severity.value,
                    "region": case.incident.region,
                    "started_at": case.incident.started_at.isoformat(
                        timespec="minutes"
                    )
                    if case.incident.started_at
                    else None,
                    "symptoms": list(case.incident.symptoms),
                    "suspected_dependencies": list(
                        case.incident.suspected_dependencies
                    ),
                    "tech_context": list(case.incident.tech_context),
                    "events": [
                        {
                            "at": event.at.isoformat(timespec="minutes"),
                            "type": event.type.value,
                            "service": event.service,
                            "description": event.description,
                        }
                        for event in case.incident.events
                    ],
                    "log_excerpt": case.incident.log_excerpt,
                },
            }
        )
        for case in load_eval_cases()
    ]


@router.get("/stats", response_model=CorpusStatsOut)
async def stats(
    repo: RepositoryDep, store: VectorStoreDep, settings: SettingsDep
) -> CorpusStatsOut:
    return CorpusStatsOut(
        counts=repo.counts(),
        indexed_chunks=store.count(),
        supported_artifact_kinds=sorted(k.value for k in SUPPORTED_ARTIFACT_KINDS),
        reserved_artifact_kinds=sorted(k.value for k in RESERVED_ARTIFACT_KINDS),
        origins=repo.origins(),
        region_policy=settings.region_policy,
        eval_origin=settings.eval_origin,
    )


@router.get("/sources/{source_id}", response_model=SourceOut)
async def read_source(source_id: str, repo: RepositoryDep) -> SourceOut:
    """Resolve a citation to the document it points at."""
    for resolve in (_incident, _postmortem, _runbook, _log):
        found = resolve(repo, source_id)
        if found:
            return found
    raise HTTPException(status_code=404, detail=f"No source with id {source_id}")


def _incident(repo, source_id: str) -> SourceOut | None:
    incident = repo.incident(source_id)
    if not incident:
        return None

    body = [
        incident.summary,
        "",
        "Symptoms:",
        *(f"  - {s}" for s in incident.symptoms),
        "",
        "Timeline:",
        *(
            f"  {e.at:%Y-%m-%d %H:%M}  {e.type.value}  [{e.service}]  {e.description}"
            for e in incident.events
        ),
        "",
        f"Root cause: {incident.root_cause}",
        "",
        f"Resolution: {incident.resolution}",
    ]
    if incident.actions:
        body += ["", "Actions taken:", *(f"  - {a.describe()}" for a in incident.actions)]

    return SourceOut(
        source_id=incident.incident_id,
        source_type="incident",
        title=incident.title,
        body="\n".join(body),
        metadata={
            "severity": incident.severity.value,
            "service": incident.service,
            "environment": incident.environment.value,
            "region": incident.region,
            "started_at": incident.started_at.isoformat(timespec="minutes"),
            "duration_minutes": str(incident.duration_minutes),
            "stack": ", ".join(incident.tech_context) or None,
            "origin": incident.origin,
        },
    )


def _postmortem(repo, source_id: str) -> SourceOut | None:
    postmortem = repo.postmortem(source_id)
    if not postmortem:
        return None

    return SourceOut(
        source_id=postmortem.postmortem_id,
        source_type="postmortem",
        title=f"Postmortem for {postmortem.incident_id}",
        body="\n".join(
            [
                f"Root cause: {postmortem.root_cause}",
                "",
                f"Impact: {postmortem.impact}",
                "",
                f"Resolution: {postmortem.resolution}",
                "",
                "Lessons learned:",
                *(f"  - {lesson}" for lesson in postmortem.lessons_learned),
            ]
        ),
        metadata={
            "incident_id": postmortem.incident_id,
            "written_at": postmortem.written_at.isoformat(timespec="minutes"),
            "origin": postmortem.origin,
        },
    )


def _runbook(repo, source_id: str) -> SourceOut | None:
    runbook = repo.runbook(source_id)
    if not runbook:
        return None

    return SourceOut(
        source_id=runbook.runbook_id,
        source_type="runbook",
        title=runbook.title,
        body="\n".join(
            [
                f"Trigger: {runbook.trigger}",
                "",
                "Steps:",
                *(
                    f"  {n}. {step}"
                    for n, step in enumerate(runbook.steps, start=1)
                ),
            ]
        ),
        metadata={
            "service": runbook.service,
            "version": runbook.version,
            "updated_at": runbook.updated_at.isoformat(timespec="minutes"),
            "escalate_to": runbook.escalate_to,
            "origin": runbook.origin,
        },
    )


def _log(repo, source_id: str) -> SourceOut | None:
    log = repo.log_excerpt(source_id)
    if not log:
        return None

    return SourceOut(
        source_id=log.log_id,
        source_type="log_excerpt",
        title=f"{log.service} log excerpt from {log.incident_id}",
        body="\n".join(log.lines),
        metadata={
            "incident_id": log.incident_id,
            "service": log.service,
            "captured_at": log.captured_at.isoformat(timespec="minutes"),
            "origin": log.origin,
        },
    )
