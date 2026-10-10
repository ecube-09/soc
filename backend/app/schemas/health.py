from typing import Literal

from pydantic import BaseModel, Field

HealthStatus = Literal["ok", "degraded"]


class ModelCheck(BaseModel):
    name: str
    role: Literal["chat", "embedding"]
    present: bool


class RuntimeCheck(BaseModel):
    provider: str = "ollama"
    base_url: str
    reachable: bool
    detail: str | None = None
    models: list[ModelCheck] = Field(default_factory=list)
    available_models: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: HealthStatus
    app_name: str
    version: str
    runtime: RuntimeCheck
