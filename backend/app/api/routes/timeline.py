from datetime import date

from fastapi import APIRouter, HTTPException

from app.analysis import log_events
from app.api.deps import RepositoryDep
from app.schemas.investigation import TimelineExtractOut, TimelineExtractRequest

router = APIRouter(tags=["timeline"])


@router.post("/timeline/extract", response_model=TimelineExtractOut)
async def extract_timeline(
    request: TimelineExtractRequest, repo: RepositoryDep
) -> TimelineExtractOut:
    """Propose typed events from a log paste. Proposals only; nothing is saved."""
    try:
        on_date = date.fromisoformat(request.date[:10]) if request.date else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Bad date: {request.date}") from exc

    extraction = log_events.extract(
        request.log_text,
        service=request.service,
        known_services=tuple(s.name for s in repo.services()),
        on_date=on_date,
    )
    return TimelineExtractOut.model_validate(extraction.to_dict())
