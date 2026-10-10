from typing import Annotated

from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.db import SqliteRepository
from app.llm import LLMProvider
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
