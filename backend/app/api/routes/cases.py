"""Cases and artifacts.

A case is the unit of work, and the current incident is its first artifact.
Later uploads - logs, packet captures, suspicious files, security documents -
attach to the same case rather than to a second application. Those kinds are
declared here and rejected on purpose, so the boundary is visible in the API
instead of implied by what is missing.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.api.deps import RepositoryDep
from app.domain import ArtifactKind, assert_artifact_kind_supported
from app.schemas.investigation import ArtifactOut, ArtifactRequest

router = APIRouter(tags=["cases"])


@router.get("/cases/{case_id}/artifacts", response_model=list[ArtifactOut])
async def list_artifacts(case_id: str, repo: RepositoryDep) -> list[ArtifactOut]:
    return [
        ArtifactOut(
            artifact_id=artifact["artifact_id"],
            case_id=case_id,
            kind=artifact["kind"],
            created_at=artifact["created_at"],
        )
        for artifact in repo.case_artifacts(case_id)
    ]


@router.post("/cases/{case_id}/artifacts", response_model=ArtifactOut, status_code=201)
async def add_artifact(
    case_id: str, request: ArtifactRequest, repo: RepositoryDep
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

    now = datetime.now()
    artifact_id = f"ART-{uuid.uuid4().hex[:8].upper()}"
    repo.create_case(case_id, f"Case {case_id}", now)
    repo.add_artifact(
        artifact_id=artifact_id,
        case_id=case_id,
        kind=kind.value,
        payload=request.payload,
        created_at=now,
    )

    return ArtifactOut(
        artifact_id=artifact_id,
        case_id=case_id,
        kind=kind.value,
        created_at=now.isoformat(timespec="seconds"),
    )
