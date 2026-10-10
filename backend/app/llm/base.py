from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class LLMError(RuntimeError):
    """Raised when the model runtime is unreachable or returns an unusable response."""


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class RuntimeStatus:
    """What the health endpoint needs to know about the model runtime."""

    reachable: bool
    available_models: list[str] = field(default_factory=list)
    detail: str | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """The only model surface the rest of the app is allowed to depend on.

    Swapping Ollama for another OpenAI-compatible server is a new implementation
    of this protocol plus a config change, not a second integration.
    """

    chat_model: str
    embedding_model: str

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> str: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def status(self) -> RuntimeStatus: ...

    async def aclose(self) -> None: ...
