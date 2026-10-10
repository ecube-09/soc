from fastapi import APIRouter

from app.api.routes import cases, corpus, health, investigations, timeline

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(corpus.router)
api_router.include_router(investigations.router)
api_router.include_router(cases.router)
api_router.include_router(timeline.router)
