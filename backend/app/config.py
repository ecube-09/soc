from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INCIDENT_",
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Historical Incident and Runbook Assistant"
    api_prefix: str = "/api/v1"

    ollama_base_url: str = "http://127.0.0.1:11434"
    chat_model: str = "qwen2.5:7b"
    embedding_model: str = "nomic-embed-text"
    ollama_timeout_seconds: float = 120.0
    ollama_health_timeout_seconds: float = 5.0

    sqlite_path: Path = Path("data/incidents.db")
    chroma_path: Path = Path("data/chroma")
    chroma_collection: str = "incident_documents"

    chunk_target_chars: int = 900
    semantic_candidates: int = 60
    runbook_candidates: int = 24
    max_comparable_cases: int = 5
    max_runbooks: int = 3

    # Weights for the candidate score. Retrieval is explainable on purpose:
    # every component is reported alongside the total.
    weight_semantic: float = 0.32
    weight_metadata: float = 0.24
    weight_timeline_sequence: float = 0.16
    weight_timeline_interval: float = 0.08
    weight_recency: float = 0.12
    weight_service_graph: float = 0.08

    # A runbook is chosen mostly on whether its stated trigger describes the
    # symptoms. Service scope is a tiebreak, not the deciding term.
    runbook_weight_semantic: float = 0.45
    runbook_weight_trigger: float = 0.35
    runbook_weight_applicability: float = 0.20

    # A case older than this is still shown, but flagged: its remediation may
    # target a stack that no longer exists.
    stale_after_days: int = 540
    recency_half_life_days: float = 120.0

    # Below this the system refuses to name a cause and escalates instead.
    confidence_threshold: float = 0.45
    min_supporting_cases: int = 1
    conflict_confidence_penalty: float = 0.15

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def sqlite_abs_path(self) -> Path:
        return self._resolve(self.sqlite_path)

    @property
    def chroma_abs_path(self) -> Path:
        return self._resolve(self.chroma_path)

    @staticmethod
    def _resolve(value: Path) -> Path:
        return value if value.is_absolute() else BACKEND_ROOT / value


@lru_cache
def get_settings() -> Settings:
    return Settings()
