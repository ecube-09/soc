"""Operator commands: seed the corpus, build the index, and run a case offline.

Every step of the pipeline is runnable on its own so retrieval can be judged
without the model in the way.
"""

import argparse
import asyncio
import json
from collections.abc import Sequence

from app.config import get_settings
from app.corpus import load_corpus, load_eval_cases
from app.db import SqliteRepository
from app.ingest import build_documents, index_documents
from app.llm import build_provider
from app.retrieval import HybridRetriever
from app.vectors import ChromaVectorStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed", help="load the authored corpus into SQLite")
    sub.add_parser("stats", help="row counts per table")

    sub.add_parser("ingest", help="chunk, embed, and index the corpus")

    retrieve = sub.add_parser(
        "retrieve", help="run retrieval and the gate for an eval case, no generation"
    )
    retrieve.add_argument("case_id")
    retrieve.add_argument("--json", action="store_true")

    investigate = sub.add_parser(
        "investigate", help="run the full pipeline for an eval case"
    )
    investigate.add_argument("case_id")
    investigate.add_argument("--json", action="store_true")

    evaluate = sub.add_parser("eval", help="score retrieval and gate behaviour")
    evaluate.add_argument("--json", action="store_true")
    evaluate.add_argument(
        "--with-generation",
        action="store_true",
        help="also run the model and check citation validity (slow)",
    )

    args = parser.parse_args(argv)
    return _COMMANDS[args.command](args)


def _seed(_: argparse.Namespace) -> int:
    settings = get_settings()
    corpus = load_corpus()
    repo = SqliteRepository(settings.sqlite_abs_path)
    try:
        repo.init_schema()
        repo.replace_corpus(
            services=corpus.services,
            incidents=corpus.incidents,
            runbooks=corpus.runbooks,
            postmortems=corpus.postmortems,
            log_excerpts=corpus.log_excerpts,
        )
        # Chunks are derived from the corpus; leaving the old ones would let the
        # index disagree with the knowledge it was built from.
        repo.replace_documents([])
        counts = repo.counts()
    finally:
        repo.close()

    print(f"Seeded {settings.sqlite_abs_path}")
    _print_counts(counts)
    return 0


def _stats(_: argparse.Namespace) -> int:
    settings = get_settings()
    repo = SqliteRepository(settings.sqlite_abs_path)
    try:
        repo.init_schema()
        _print_counts(repo.counts())
    finally:
        repo.close()
    return 0


def _ingest(_: argparse.Namespace) -> int:
    return asyncio.run(_ingest_async())


async def _ingest_async() -> int:
    settings = get_settings()
    repo = SqliteRepository(settings.sqlite_abs_path)
    provider = build_provider(settings)
    store = ChromaVectorStore(settings.chroma_abs_path, settings.chroma_collection)
    try:
        documents = build_documents(repo)
        repo.replace_documents(documents)
        print(f"Built {len(documents)} chunks; embedding with {provider.embedding_model}")
        count = await index_documents(documents, provider=provider, store=store)
        print(f"Indexed {count} chunks into {settings.chroma_abs_path}")
    finally:
        await provider.aclose()
        repo.close()
    return 0


def _retrieve(args: argparse.Namespace) -> int:
    return asyncio.run(_retrieve_async(args))


async def _retrieve_async(args: argparse.Namespace) -> int:
    case = _eval_case(args.case_id)
    settings = get_settings()
    repo = SqliteRepository(settings.sqlite_abs_path)
    provider = build_provider(settings)
    store = ChromaVectorStore(settings.chroma_abs_path, settings.chroma_collection)
    try:
        retriever = HybridRetriever(repo, store, provider, settings)
        pack = await retriever.build_evidence(case.incident)
    finally:
        await provider.aclose()
        repo.close()

    if args.json:
        print(json.dumps(pack.to_dict(), indent=2, default=str))
        return 0

    print(f"{case.case_id}: {case.incident.title}")
    print(f"gate: mode={pack.mode.value} confidence={pack.confidence:.2f}")
    print(f"reason: {pack.gate_reason}")
    print("\ncomparable cases")
    for candidate in pack.candidates:
        flags = " ".join(f"[{f}]" for f in candidate.flags)
        print(
            f"  {candidate.incident.incident_id}  score={candidate.score:.3f}"
            f"  sem={candidate.semantic:.2f} meta={candidate.metadata_score:.2f}"
            f" seq={candidate.timeline_sequence:.2f} int={candidate.timeline_interval:.2f}"
            f" rec={candidate.recency:.2f}  {flags}"
        )
        print(f"      {candidate.incident.title}")
    print("\nrunbooks")
    for runbook in pack.runbooks:
        print(f"  {runbook.runbook.runbook_id}  score={runbook.score:.3f}  {runbook.runbook.title}")
    if pack.conflicts:
        print("\nconflicts")
        for conflict in pack.conflicts:
            print(f"  {conflict.target}: {conflict.summary}")
    if pack.unknowns:
        print("\nunknowns")
        for unknown in pack.unknowns:
            print(f"  - {unknown}")
    return 0


def _investigate(args: argparse.Namespace) -> int:
    return asyncio.run(_investigate_async(args))


async def _investigate_async(args: argparse.Namespace) -> int:
    from app.services.investigation import InvestigationService

    case = _eval_case(args.case_id)
    settings = get_settings()
    repo = SqliteRepository(settings.sqlite_abs_path)
    provider = build_provider(settings)
    store = ChromaVectorStore(settings.chroma_abs_path, settings.chroma_collection)
    try:
        service = InvestigationService(
            HybridRetriever(repo, store, provider, settings), provider, repo, settings
        )
        result = await service.investigate(case.incident)
    finally:
        await provider.aclose()
        repo.close()

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
        return 0

    print(f"{case.case_id}: {case.incident.title}")
    print(f"mode: {result.mode.value}   root cause: {result.root_cause_status.value}")
    print(f"confidence: {result.confidence:.2f} ({result.gate_reason})\n")
    print(result.render_text())
    return 0


def _eval(args: argparse.Namespace) -> int:
    from app.eval import run_eval

    return asyncio.run(
        run_eval(as_json=args.json, with_generation=args.with_generation)
    )


def _eval_case(case_id: str):
    cases = {case.case_id: case for case in load_eval_cases()}
    if case_id not in cases:
        raise SystemExit(
            f"Unknown case {case_id}. Available: {', '.join(sorted(cases))}"
        )
    return cases[case_id]


def _print_counts(counts: dict[str, int]) -> None:
    width = max(len(name) for name in counts)
    for name, count in counts.items():
        print(f"  {name.ljust(width)}  {count}")


_COMMANDS = {
    "seed": _seed,
    "stats": _stats,
    "ingest": _ingest,
    "retrieve": _retrieve,
    "investigate": _investigate,
    "eval": _eval,
}

if __name__ == "__main__":
    raise SystemExit(main())
