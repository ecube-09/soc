from app.config import Settings
from app.llm.base import ChatMessage, LLMError, LLMProvider, RuntimeStatus
from app.llm.ollama import OllamaProvider, normalize_model_name

__all__ = [
    "ChatMessage",
    "LLMError",
    "LLMProvider",
    "OllamaProvider",
    "RuntimeStatus",
    "build_provider",
    "normalize_model_name",
]


def build_provider(settings: Settings) -> LLMProvider:
    return OllamaProvider(
        base_url=settings.ollama_base_url,
        chat_model=settings.chat_model,
        embedding_model=settings.embedding_model,
        timeout_seconds=settings.ollama_timeout_seconds,
        health_timeout_seconds=settings.ollama_health_timeout_seconds,
    )
