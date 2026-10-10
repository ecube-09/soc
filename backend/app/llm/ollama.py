from typing import Any

import httpx

from app.llm.base import ChatMessage, LLMError, RuntimeStatus


def normalize_model_name(name: str) -> str:
    """Ollama reports untagged pulls as ``name:latest``; treat the two as one model."""
    return name if ":" in name else f"{name}:latest"


class OllamaProvider:
    """LLMProvider backed by the local Ollama HTTP service."""

    def __init__(
        self,
        base_url: str,
        chat_model: str,
        embedding_model: str,
        *,
        timeout_seconds: float = 120.0,
        health_timeout_seconds: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.embedding_model = embedding_model
        self.health_timeout_seconds = health_timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout_seconds
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.chat_model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": temperature},
        }
        if json_schema is not None:
            payload["format"] = json_schema

        data = await self._post("/api/chat", payload)
        content = data.get("message", {}).get("content")
        if not isinstance(content, str):
            raise LLMError("Ollama chat response did not contain message content")
        return content

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        data = await self._post(
            "/api/embed", {"model": self.embedding_model, "input": texts}
        )
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise LLMError(
                f"Ollama returned {len(embeddings) if isinstance(embeddings, list) else 0} "
                f"embeddings for {len(texts)} inputs"
            )
        return embeddings

    async def status(self) -> RuntimeStatus:
        try:
            response = await self._client.get(
                "/api/tags", timeout=self.health_timeout_seconds
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return RuntimeStatus(reachable=False, detail=f"{type(exc).__name__}: {exc}")

        models = response.json().get("models") or []
        names = [m["name"] for m in models if isinstance(m, dict) and "name" in m]
        return RuntimeStatus(reachable=True, available_models=sorted(names))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"Ollama {path} returned {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama {path} unreachable: {exc}") from exc
