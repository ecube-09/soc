from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.config import Settings, get_settings
from app.db import SqliteRepository
from app.llm import LLMProvider
from app.retrieval import HybridRetriever
from app.services import InvestigationService
from app.services.cases import CaseService
from app.vectors import VectorStore


def get_provider(request: Request) -> LLMProvider:
    return request.app.state.provider


def get_repository(request: Request) -> SqliteRepository:
    return request.app.state.repository


def get_vector_store(request: Request) -> VectorStore:
    return request.app.state.vector_store


SettingsDep = Annotated[Settings, Depends(get_settings)]
ProviderDep = Annotated[LLMProvider, Depends(get_provider)]
RepositoryDep = Annotated[SqliteRepository, Depends(get_repository)]
VectorStoreDep = Annotated[VectorStore, Depends(get_vector_store)]


def get_case_service(
    settings: SettingsDep,
    provider: ProviderDep,
    repo: RepositoryDep,
    store: VectorStoreDep,
) -> CaseService:
    return CaseService(
        repo,
        InvestigationService(
            HybridRetriever(repo, store, provider, settings), provider, repo, settings
        ),
    )


def require_index(store: VectorStoreDep) -> None:
    if store.count() == 0:
        raise HTTPException(
            status_code=503,
            detail=(
                "The corpus is not indexed. Run `python -m app.cli seed` and "
                "`python -m app.cli ingest` first."
            ),
        )


CaseServiceDep = Annotated[CaseService, Depends(get_case_service)]
