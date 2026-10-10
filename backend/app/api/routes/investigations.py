from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import CaseServiceDep, require_index
from app.schemas.investigation import InvestigationRequest, InvestigationResponse

router = APIRouter(tags=["investigations"])


@router.post(
    "/investigations",
    response_model=InvestigationResponse,
    dependencies=[Depends(require_index)],
)
async def create_investigation(
    request: InvestigationRequest, cases: CaseServiceDep
) -> InvestigationResponse:
    """Investigate a current incident against the historical corpus.

    Opens a case (or adds to the one named), stores the incident as its first
    artifact, and stores the result as revision 1. Returns a cited investigation
    plan, or an escalation report when the evidence does not support naming a
    cause. Never applies anything.
    """
    try:
        body = await cases.start(
            request.incident.model_dump(mode="json"),
            author=request.author,
            case_id=request.case_id,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return InvestigationResponse.model_validate(body)
