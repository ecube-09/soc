import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.api.deps import ProviderDep, RepositoryDep, SettingsDep, VectorStoreDep
from app.corpus import parse_current_incident
from app.domain import ArtifactKind
from app.retrieval import HybridRetriever
from app.schemas.investigation import InvestigationRequest, InvestigationResponse
from app.services import InvestigationService

router = APIRouter(tags=["investigations"])


@router.post("/investigations", response_model=InvestigationResponse)
async def create_investigation(
    request: InvestigationRequest,
    settings: SettingsDep,
    provider: ProviderDep,
    repo: RepositoryDep,
    store: VectorStoreDep,
) -> InvestigationResponse:
    """Investigate a current incident against the historical corpus.

    Returns a cited investigation plan, or an escalation report when the
    evidence does not support naming a cause. Never applies anything.
    """
    try:
        current = parse_current_incident(request.incident.model_dump(mode="json"))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if store.count() == 0:
        raise HTTPException(
            status_code=503,
            detail=(
                "The corpus is not indexed. Run `python -m app.cli seed` and "
                "`python -m app.cli ingest` first."
            ),
        )

    now = datetime.now()
    case_id = request.case_id or f"CASE-{uuid.uuid4().hex[:8].upper()}"
    repo.create_case(case_id, current.title, now)
    repo.add_artifact(
        artifact_id=f"ART-{uuid.uuid4().hex[:8].upper()}",
        case_id=case_id,
        kind=ArtifactKind.INCIDENT.value,
        payload=request.incident.model_dump(mode="json"),
        created_at=now,
    )

    service = InvestigationService(
        HybridRetriever(repo, store, provider, settings), provider, repo, settings
    )
    result = await service.investigate(current)

    return InvestigationResponse.model_validate(
        {**result.to_dict(), "case_id": case_id, "rendered": result.render_text()}
    )
