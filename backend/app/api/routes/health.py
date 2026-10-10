from fastapi import APIRouter

from app import __version__
from app.api.deps import ProviderDep, SettingsDep
from app.llm import normalize_model_name
from app.schemas.health import HealthResponse, ModelCheck, RuntimeCheck

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(settings: SettingsDep, provider: ProviderDep) -> HealthResponse:
    """Report whether Ollama is up and both configured models are pulled.

    Always 200: the UI needs the detail to tell the operator what to fix.
    """
    status = await provider.status()
    installed = {normalize_model_name(name) for name in status.available_models}

    models = [
        ModelCheck(
            name=settings.chat_model,
            role="chat",
            present=normalize_model_name(settings.chat_model) in installed,
        ),
        ModelCheck(
            name=settings.embedding_model,
            role="embedding",
            present=normalize_model_name(settings.embedding_model) in installed,
        ),
    ]

    runtime = RuntimeCheck(
        base_url=settings.ollama_base_url,
        reachable=status.reachable,
        detail=status.detail,
        models=models,
        available_models=status.available_models,
    )
    healthy = status.reachable and all(m.present for m in models)

    return HealthResponse(
        status="ok" if healthy else "degraded",
        app_name=settings.app_name,
        version=__version__,
        runtime=runtime,
    )
