from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_provider
from app.llm.base import RuntimeStatus
from app.main import create_app


class StubProvider:
    """Stands in for OllamaProvider so health can be tested without a running runtime."""

    chat_model = "qwen2.5:7b"
    embedding_model = "nomic-embed-text"

    def __init__(self, status: RuntimeStatus) -> None:
        self._status = status

    async def status(self) -> RuntimeStatus:
        return self._status

    async def aclose(self) -> None:
        return None


@pytest.fixture
def make_client(settings):
    def factory(status: RuntimeStatus) -> Iterator[TestClient]:
        app = create_app(settings)
        app.dependency_overrides[get_provider] = lambda: StubProvider(status)
        return TestClient(app)

    return factory


def test_health_ok_when_runtime_up_and_both_models_present(make_client):
    status = RuntimeStatus(
        reachable=True,
        available_models=["llama3.2:1b", "nomic-embed-text:latest", "qwen2.5:7b"],
    )
    with make_client(status) as client:
        body = client.get("/api/v1/health").json()

    assert body["status"] == "ok"
    assert body["runtime"]["reachable"] is True
    assert {m["role"]: m["present"] for m in body["runtime"]["models"]} == {
        "chat": True,
        "embedding": True,
    }


def test_health_degraded_when_embedding_model_missing(make_client):
    status = RuntimeStatus(reachable=True, available_models=["qwen2.5:7b"])
    with make_client(status) as client:
        response = client.get("/api/v1/health")

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "degraded"
    assert [m["name"] for m in body["runtime"]["models"] if not m["present"]] == [
        "nomic-embed-text"
    ]


def test_health_degraded_when_runtime_unreachable(make_client):
    status = RuntimeStatus(reachable=False, detail="ConnectError: connection refused")
    with make_client(status) as client:
        body = client.get("/api/v1/health").json()

    assert body["status"] == "degraded"
    assert body["runtime"]["reachable"] is False
    assert "connection refused" in body["runtime"]["detail"]
    assert all(not m["present"] for m in body["runtime"]["models"])


def test_startup_creates_data_directories(settings):
    with TestClient(create_app(settings)):
        pass

    assert settings.sqlite_abs_path.parent.is_dir()
    assert settings.chroma_abs_path.is_dir()
