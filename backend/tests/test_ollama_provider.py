import httpx
import pytest

from app.llm import ChatMessage, LLMError, OllamaProvider, normalize_model_name

BASE_URL = "http://127.0.0.1:11434"


def provider_with(handler) -> OllamaProvider:
    return OllamaProvider(
        BASE_URL,
        "qwen2.5:7b",
        "nomic-embed-text",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url=BASE_URL
        ),
    )


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("nomic-embed-text", "nomic-embed-text:latest"), ("qwen2.5:7b", "qwen2.5:7b")],
)
def test_normalize_model_name_defaults_the_latest_tag(configured, expected):
    assert normalize_model_name(configured) == expected


async def test_status_lists_installed_models():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "qwen2.5:7b"},
                    {"name": "nomic-embed-text:latest"},
                ]
            },
        )

    status = await provider_with(handler).status()

    assert status.reachable is True
    assert status.available_models == ["nomic-embed-text:latest", "qwen2.5:7b"]


async def test_status_reports_unreachable_runtime():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    status = await provider_with(refuse).status()

    assert status.reachable is False
    assert status.available_models == []
    assert "connection refused" in (status.detail or "")


async def test_chat_sends_schema_and_returns_content():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": '{"ok": true}'}})

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    content = await provider_with(handler).chat(
        [ChatMessage(role="user", content="hi")], json_schema=schema
    )

    assert content == '{"ok": true}'
    assert captured["model"] == "qwen2.5:7b"
    assert captured["stream"] is False
    assert captured["format"] == schema
    assert captured["options"]["temperature"] == 0.0


async def test_embed_returns_one_vector_per_input():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})

    vectors = await provider_with(handler).embed(["a", "b"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]


async def test_embed_skips_the_call_for_no_inputs():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not call Ollama for an empty batch")

    assert await provider_with(handler).embed([]) == []


async def test_embed_raises_when_count_does_not_match():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})

    with pytest.raises(LLMError, match="1 embeddings for 2 inputs"):
        await provider_with(handler).embed(["a", "b"])


async def test_chat_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="model not found")

    with pytest.raises(LLMError, match="404"):
        await provider_with(handler).chat([ChatMessage(role="user", content="hi")])
