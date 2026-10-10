"""Turn structured knowledge into retrievable chunks, then embed them.

Chunks are cut on authored section boundaries rather than at a fixed width, so
every chunk stays about one thing and carries the id a citation must point at.
"""

from collections.abc import Iterable, Sequence

from app.db.repository import SqliteRepository
from app.domain import (
    Document,
    Environment,
    Incident,
    LogExcerpt,
    Postmortem,
    Runbook,
    SourceType,
)
from app.llm import LLMProvider
from app.vectors import VectorStore

EMBED_BATCH_SIZE = 16


def build_documents(
    repo: SqliteRepository, *, target_chars: int = 900
) -> list[Document]:
    documents: list[Document] = []
    for incident in repo.incidents():
        documents.extend(_incident_documents(incident, target_chars))
        for log in repo.log_excerpts_for(incident.incident_id):
            documents.extend(_log_documents(log, incident, target_chars))
    for postmortem in repo.postmortems():
        incident = repo.incident(postmortem.incident_id)
        documents.extend(_postmortem_documents(postmortem, incident, target_chars))
    for runbook in repo.runbooks():
        documents.extend(_runbook_documents(runbook, target_chars))
    return documents


async def index_documents(
    documents: Sequence[Document],
    *,
    provider: LLMProvider,
    store: VectorStore,
    batch_size: int = EMBED_BATCH_SIZE,
) -> int:
    store.reset()
    indexed = 0
    for batch in _batched(documents, batch_size):
        embeddings = await provider.embed([embedding_text(d) for d in batch])
        store.upsert(
            ids=[d.document_id for d in batch],
            embeddings=embeddings,
            metadatas=[document_metadata(d) for d in batch],
            texts=[d.text for d in batch],
        )
        indexed += len(batch)
    return indexed


def embedding_text(document: Document) -> str:
    """The title and section stay in the embedded text; they carry real signal."""
    header = document.title
    if document.section:
        header = f"{header} — {document.section}"
    return f"{header}\n{document.text}"


def document_metadata(document: Document) -> dict[str, object]:
    return {
        "source_type": document.source_type.value,
        "source_id": document.source_id,
        "service": document.service,
        "environment": document.environment.value if document.environment else None,
        "section": document.section,
        "updated_at": document.updated_at.isoformat(timespec="seconds"),
    }


def _incident_documents(incident: Incident, target_chars: int) -> list[Document]:
    metadata_line = (
        f"{incident.severity.value} · {incident.service} · {incident.environment.value}"
        f" · {incident.region} · started {incident.started_at:%Y-%m-%d %H:%M}"
    )
    sections: list[tuple[str, str]] = [
        (
            "overview",
            "\n".join(
                [
                    metadata_line,
                    incident.summary,
                    _bullets("Symptoms", incident.symptoms),
                    _bullets("Related services", incident.related_services),
                    _bullets("Stack at the time", incident.tech_context),
                ]
            ),
        ),
        (
            "timeline",
            "\n".join(
                f"{event.at:%H:%M} {event.type.value} [{event.service}] {event.description}"
                for event in incident.events
            ),
        ),
        ("root cause", incident.root_cause),
        (
            "resolution",
            "\n".join(
                [incident.resolution, _bullets("Actions", [a.describe() for a in incident.actions])]
            ),
        ),
    ]
    return _documents(
        source_type=SourceType.INCIDENT,
        source_id=incident.incident_id,
        title=f"{incident.incident_id} {incident.title}",
        sections=sections,
        service=incident.service,
        environment=incident.environment,
        updated_at=incident.started_at,
        target_chars=target_chars,
    )


def _postmortem_documents(
    postmortem: Postmortem, incident: Incident | None, target_chars: int
) -> list[Document]:
    sections = [
        ("root cause", postmortem.root_cause),
        ("impact", postmortem.impact),
        ("resolution", postmortem.resolution),
        ("lessons learned", _bullets(None, postmortem.lessons_learned)),
    ]
    title = f"{postmortem.postmortem_id} postmortem for {postmortem.incident_id}"
    if incident:
        title = f"{title}: {incident.title}"
    return _documents(
        source_type=SourceType.POSTMORTEM,
        source_id=postmortem.postmortem_id,
        title=title,
        sections=sections,
        service=incident.service if incident else None,
        environment=incident.environment if incident else None,
        updated_at=postmortem.written_at,
        target_chars=target_chars,
    )


def _runbook_documents(runbook: Runbook, target_chars: int) -> list[Document]:
    sections = [
        (
            "trigger",
            f"Trigger: {runbook.trigger}\nApplies to: {runbook.service or 'any service'}"
            f"\nEscalate to: {runbook.escalate_to or 'not specified'}",
        ),
        (
            "steps",
            "\n".join(f"{n}. {step}" for n, step in enumerate(runbook.steps, start=1)),
        ),
    ]
    return _documents(
        source_type=SourceType.RUNBOOK,
        source_id=runbook.runbook_id,
        title=f"{runbook.runbook_id} {runbook.title} (v{runbook.version})",
        sections=sections,
        service=runbook.service,
        # Runbooks are not environment-specific; leaving this unset keeps them
        # eligible under an environment filter.
        environment=None,
        updated_at=runbook.updated_at,
        target_chars=target_chars,
    )


def _log_documents(
    log: LogExcerpt, incident: Incident, target_chars: int
) -> list[Document]:
    return _documents(
        source_type=SourceType.LOG_EXCERPT,
        source_id=log.log_id,
        title=f"{log.log_id} {log.service} log excerpt from {log.incident_id}",
        sections=[("lines", "\n".join(log.lines))],
        service=log.service,
        environment=incident.environment,
        updated_at=log.captured_at,
        target_chars=target_chars,
    )


def _documents(
    *,
    source_type: SourceType,
    source_id: str,
    title: str,
    sections: Sequence[tuple[str, str]],
    service: str | None,
    environment: Environment | None,
    updated_at,
    target_chars: int,
) -> list[Document]:
    documents: list[Document] = []
    for section, body in sections:
        text = body.strip()
        if not text:
            continue
        for index, part in enumerate(_split(text, target_chars)):
            suffix = f"{section}-{index}" if index else section
            documents.append(
                Document(
                    document_id=f"{source_id}::{suffix}",
                    source_type=source_type,
                    source_id=source_id,
                    title=title,
                    section=section,
                    text=part,
                    service=service,
                    environment=environment,
                    updated_at=updated_at,
                )
            )
    return documents


def _split(text: str, target_chars: int) -> list[str]:
    """Split an oversized section on line boundaries, never mid-line."""
    if len(text) <= target_chars:
        return [text]

    parts: list[str] = []
    current: list[str] = []
    length = 0
    for line in text.splitlines():
        if current and length + len(line) > target_chars:
            parts.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        parts.append("\n".join(current))
    return parts


def _bullets(label: str | None, values: Iterable[str]) -> str:
    items = [v for v in values if v]
    if not items:
        return ""
    body = "\n".join(f"- {item}" for item in items)
    return f"{label}:\n{body}" if label else body


def _batched(items: Sequence[Document], size: int) -> Iterable[Sequence[Document]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
