"""Chunk embedding storage. Chroma now, behind a two-method seam."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class VectorHit:
    document_id: str
    similarity: float
    metadata: dict[str, Any]


class VectorStore(Protocol):
    def upsert(
        self,
        *,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        metadatas: Sequence[dict[str, Any]],
        texts: Sequence[str],
    ) -> None: ...

    def query(
        self,
        embedding: Sequence[float],
        *,
        limit: int,
        where: dict[str, Any] | None = None,
    ) -> list[VectorHit]: ...

    def count(self) -> int: ...

    def reset(self) -> None: ...


class ChromaVectorStore:
    def __init__(self, path: Path, collection: str) -> None:
        import chromadb

        path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(path))
        self._collection_name = collection
        self._collection = self._client.get_or_create_collection(
            name=collection, metadata={"hnsw:space": "cosine"}
        )

    def upsert(
        self,
        *,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        metadatas: Sequence[dict[str, Any]],
        texts: Sequence[str],
    ) -> None:
        if not ids:
            return
        self._collection.upsert(
            ids=list(ids),
            embeddings=[list(e) for e in embeddings],
            metadatas=[_clean(m) for m in metadatas],
            documents=list(texts),
        )

    def query(
        self,
        embedding: Sequence[float],
        *,
        limit: int,
        where: dict[str, Any] | None = None,
    ) -> list[VectorHit]:
        if self._collection.count() == 0:
            return []

        result = self._collection.query(
            query_embeddings=[list(embedding)],
            n_results=min(limit, self._collection.count()),
            where=where,
        )
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]

        return [
            VectorHit(
                document_id=document_id,
                # Cosine distance in [0, 2]; the sign matters more than the scale.
                similarity=max(0.0, 1.0 - float(distance)),
                metadata=dict(metadata or {}),
            )
            for document_id, distance, metadata in zip(
                ids, distances, metadatas, strict=False
            )
        ]

    def count(self) -> int:
        return self._collection.count()

    def reset(self) -> None:
        self._client.delete_collection(self._collection_name)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name, metadata={"hnsw:space": "cosine"}
        )


class InMemoryVectorStore:
    """Exact cosine search. Used by tests so retrieval can be asserted without Chroma."""

    def __init__(self) -> None:
        self._rows: dict[str, tuple[list[float], dict[str, Any], str]] = {}

    def upsert(
        self,
        *,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        metadatas: Sequence[dict[str, Any]],
        texts: Sequence[str],
    ) -> None:
        for document_id, embedding, metadata, text in zip(
            ids, embeddings, metadatas, texts, strict=True
        ):
            self._rows[document_id] = (list(embedding), _clean(metadata), text)

    def query(
        self,
        embedding: Sequence[float],
        *,
        limit: int,
        where: dict[str, Any] | None = None,
    ) -> list[VectorHit]:
        hits = [
            VectorHit(
                document_id=document_id,
                similarity=_cosine(embedding, vector),
                metadata=metadata,
            )
            for document_id, (vector, metadata, _) in self._rows.items()
            if _matches(metadata, where)
        ]
        hits.sort(key=lambda hit: hit.similarity, reverse=True)
        return hits[:limit]

    def count(self) -> int:
        return len(self._rows)

    def reset(self) -> None:
        self._rows.clear()


def _clean(metadata: dict[str, Any]) -> dict[str, Any]:
    """Chroma rejects None; drop those keys rather than storing a sentinel."""
    return {k: v for k, v in metadata.items() if v is not None}


def _matches(metadata: dict[str, Any], where: dict[str, Any] | None) -> bool:
    if not where:
        return True
    for key, condition in where.items():
        if isinstance(condition, dict):
            values = condition.get("$in")
            if values is not None and metadata.get(key) not in values:
                return False
        elif metadata.get(key) != condition:
            return False
    return True


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return max(0.0, dot / (left_norm * right_norm))
