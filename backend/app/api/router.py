from fastapi import APIRouter

from app.api.routes import cases, corpus, health, investigations

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(corpus.router)
api_router.include_router(investigations.router)
api_router.include_router(cases.router)
