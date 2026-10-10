from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import api_router
from app.config import Settings, get_settings
from app.db import SqliteRepository
from app.llm import build_provider
from app.vectors import ChromaVectorStore


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.provider = build_provider(settings)
        settings.sqlite_abs_path.parent.mkdir(parents=True, exist_ok=True)
        settings.chroma_abs_path.mkdir(parents=True, exist_ok=True)
        # Schema only. The corpus is loaded by `python -m app.cli seed` and
        # indexed by `ingest`, so starting the API never silently rewrites the
        # knowledge the index was built from.
        repository = SqliteRepository(settings.sqlite_abs_path)
        repository.init_schema()
        app.state.repository = repository
        app.state.vector_store = ChromaVectorStore(
            settings.chroma_abs_path, settings.chroma_collection
        )
        try:
            yield
        finally:
            repository.close()
            await app.state.provider.aclose()

    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
