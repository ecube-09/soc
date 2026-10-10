import hashlib
import math
import re
from collections.abc import Iterator

import pytest

from app.config import Settings
from app.corpus import Corpus, load_corpus, load_eval_cases
from app.db import SqliteRepository
from app.ingest import build_documents, index_documents
from app.llm.base import ChatMessage, RuntimeStatus
from app.retrieval import HybridRetriever
from app.vectors import InMemoryVectorStore

_TOKEN = re.compile(r"[a-z0-9][a-z0-9_.-]{2,}")
_DIMENSIONS = 512


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        sqlite_path=tmp_path / "incidents.db",
        chroma_path=tmp_path / "chroma",
    )


@pytest.fixture(scope="session")
def corpus() -> Corpus:
    return load_corpus()


@pytest.fixture(scope="session")
def eval_cases():
    return {case.case_id: case for case in load_eval_cases()}


@pytest.fixture
def repo(settings, corpus) -> Iterator[SqliteRepository]:
    repository = SqliteRepository(settings.sqlite_abs_path)
    repository.init_schema()
    repository.replace_corpus(
        services=corpus.services,
        incidents=corpus.incidents,
        runbooks=corpus.runbooks,
        postmortems=corpus.postmortems,
        log_excerpts=corpus.log_excerpts,
    )
    try:
        yield repository
    finally:
        repository.close()


class HashingProvider:
    """A deterministic stand-in for Ollama.

    Embeddings are hashed bag-of-words vectors, so cosine similarity still
    reflects shared vocabulary and retrieval order can be asserted without a
    model running. `chat` returns whatever the test queued.
    """

    chat_model = "stub-chat"
    embedding_model = "stub-embed"

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = list(responses or [])
        self.prompts: list[list[ChatMessage]] = []

    async def chat(self, messages, *, json_schema=None, temperature=0.0) -> str:
        self.prompts.append(list(messages))
        if not self.responses:
            raise AssertionError("HashingProvider.chat called with no queued response")
        return self.responses.pop(0)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_hash_embedding(text) for text in texts]

    async def status(self) -> RuntimeStatus:
        return RuntimeStatus(reachable=True, available_models=["stub-chat", "stub-embed"])

    async def aclose(self) -> None:
        return None


def _hash_embedding(text: str) -> list[float]:
    vector = [0.0] * _DIMENSIONS
    tokens = _TOKEN.findall(text.lower())
    for token in tokens:
        digest = hashlib.blake2b(token.encode(), digest_size=4).digest()
        vector[int.from_bytes(digest, "big") % _DIMENSIONS] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


@pytest.fixture
def provider() -> HashingProvider:
    return HashingProvider()


@pytest.fixture
async def retriever(repo, settings, provider) -> HybridRetriever:
    store = InMemoryVectorStore()
    await index_documents(build_documents(repo), provider=provider, store=store)
    return HybridRetriever(repo, store, provider, settings)
