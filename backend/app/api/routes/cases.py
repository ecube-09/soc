"""Cases and artifacts.

A case is the unit of work, and the current incident is its first artifact.
Notes, log excerpts, and findings attach to the same case, and a re-run turns
them into a new revision of the answer.

Packet captures, suspicious files, and security documents are declared here and
rejected on purpose, so the boundary is visible in the API instead of implied by
what is missing.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from app.api.deps import CaseServiceDep, RepositoryDep, require_index
from app.domain import ArtifactKind, assert_artifact_kind_supported
from app.schemas.investigation import (
    ArtifactDetailOut,
    ArtifactOut,
    ArtifactRequest,
    CaseDetailOut,
    CaseSummaryOut,
    InvestigationResponse,
    RerunRequest,
)
from app.services.cases import CaseNotFound

router = APIRouter(tags=["cases"])


@router.get("/cases", response_model=list[CaseSummaryOut])
async def list_cases(
    repo: RepositoryDep, limit: int = Query(default=30, ge=1, le=200)
) -> list[CaseSummaryOut]:
    return [CaseSummaryOut.model_validate(case) for case in repo.cases(limit)]


@router.get("/cases/{case_id}", response_model=CaseDetailOut)
async def get_case(case_id: str, cases: CaseServiceDep) -> CaseDetailOut:
    try:
        return CaseDetailOut.model_validate(cases.detail(case_id))
    except CaseNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/cases/{case_id}/artifacts", response_model=list[ArtifactDetailOut])
async def list_artifacts(case_id: str, repo: RepositoryDep) -> list[ArtifactDetailOut]:
    return [
        ArtifactDetailOut(case_id=case_id, **artifact)
        for artifact in repo.case_artifacts(case_id)
    ]


@router.post("/cases/{case_id}/artifacts", response_model=ArtifactOut, status_code=201)
async def add_artifact(
    case_id: str, request: ArtifactRequest, cases: CaseServiceDep
) -> ArtifactOut:
    try:
        kind = ArtifactKind(request.kind)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown artifact kind '{request.kind}'. Known kinds: "
                + ", ".join(sorted(k.value for k in ArtifactKind))
            ),
        ) from exc

    try:
        assert_artifact_kind_supported(kind)
    except ValueError as exc:
        raise HTTPException(
            status_code=501,
            detail=(
                f"{exc} This build investigates operational incidents against "
                "historical cases and runbooks. Packet captures, file samples, and "
                "security documents belong to a later analyzer, and a file sample "
                "would be stored and described, never executed."
            ),
        ) from exc

    try:
        created = cases.add_artifact(case_id, kind, request.payload, author=request.author)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ArtifactOut.model_validate(created)


@router.post(
    "/cases/{case_id}/investigations",
    response_model=InvestigationResponse,
    dependencies=[Depends(require_index)],
)
async def rerun_investigation(
    case_id: str, request: RerunRequest, cases: CaseServiceDep
) -> InvestigationResponse:
    """Re-run the pipeline with everything now attached to the case.

    The gate is free to move in either direction: a finding that favours the
    other side of a conflict can turn a hypothesis into an escalation, and one
    that settles a conflict can lift an escalation into an investigation.
    """
    try:
        body = await cases.run(
            case_id,
            author=request.author,
            incident=request.incident.model_dump(mode="json") if request.incident else None,
        )
    except CaseNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return InvestigationResponse.model_validate(body)


@router.get(
    "/cases/{case_id}/investigations/{revision}", response_model=InvestigationResponse
)
async def get_investigation(
    case_id: str, revision: int, repo: RepositoryDep
) -> InvestigationResponse:
    body = repo.investigation(case_id, revision)
    if body is None:
        raise HTTPException(
            status_code=404, detail=f"No revision {revision} on case {case_id}."
        )
    return InvestigationResponse.model_validate(body)


@router.get("/cases/{case_id}/report", response_class=PlainTextResponse)
async def export_report(
    case_id: str,
    repo: RepositoryDep,
    format: str = Query(default="markdown", pattern="^(markdown|text)$"),
    revision: int | None = None,
) -> PlainTextResponse:
    """The report as a file an on-call can send. Nothing new is computed."""
    body = repo.investigation(case_id, revision)
    if body is None:
        raise HTTPException(status_code=404, detail=f"No investigation on case {case_id}.")
    markdown = format == "markdown"
    suffix = "md" if markdown else "txt"
    return PlainTextResponse(
        body["rendered_markdown"] if markdown else body["rendered"],
        media_type="text/markdown" if markdown else "text/plain",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{case_id}-r{body["revision"]}-{body["mode"]}.{suffix}"'
            )
        },
    )
