"""Markdown import fills the same tables as the authored corpus, labelled with its origin."""

from pathlib import Path

import pytest

from app.domain import ActionDirection, EventType
from app.ingest import build_documents
from app.ingest_markdown import MarkdownImportError, import_file, parse, reimport_all

FIXTURE = Path(__file__).parent / "fixtures" / "sample_postmortem.md"


def test_markdown_postmortem_becomes_an_incident_with_events_and_actions():
    imported = parse(FIXTURE.read_text())
    incident = imported.incident

    assert incident.incident_id == "EXT-0001"
    assert incident.origin == "fixture:tests/sample_postmortem.md"
    assert incident.region == "us-east-1"
    assert [e.type for e in incident.events] == [
        EventType.DEPLOY,
        EventType.DEPENDENCY_TIMEOUT,
        EventType.ALERT,
        EventType.MITIGATION,
    ]
    assert incident.events[1].service == "checkout-api"
    assert [(a.direction, a.target) for a in incident.actions] == [
        (ActionDirection.ROLLBACK, "redis_version"),
        (ActionDirection.RECONFIGURE, "upgrade_strategy"),
    ]
    assert imported.postmortem.postmortem_id == "PM-EXT-0001"
    assert imported.postmortem.lessons_learned[0].startswith("Upgrade stateful")


def test_lines_that_cannot_be_parsed_are_reported_not_guessed():
    warnings = " ".join(parse(FIXTURE.read_text()).warnings)

    assert "dashboard was unhelpful" in warnings
    assert "fixed it somehow" in warnings


def test_synthetic_is_reserved_for_the_authored_corpus():
    with pytest.raises(MarkdownImportError, match="reserved"):
        parse(FIXTURE.read_text(), origin="synthetic")


def test_an_origin_is_required():
    text = FIXTURE.read_text().replace("origin: fixture:tests/sample_postmortem.md\n", "")
    with pytest.raises(MarkdownImportError, match="origin"):
        parse(text)


def test_a_root_cause_section_is_required():
    text = FIXTURE.read_text().replace("## Root cause", "## Thoughts")
    with pytest.raises(MarkdownImportError, match="Root cause"):
        parse(text)


def test_import_stores_and_indexes_alongside_the_corpus(repo, tmp_path):
    import_file(FIXTURE, repo, keep_dir=tmp_path)

    stored = repo.incident("EXT-0001")
    assert stored.origin.startswith("fixture:")
    assert repo.postmortem("PM-EXT-0001") is not None
    assert any(d.source_id == "EXT-0001" for d in build_documents(repo))
    assert repo.origins()["synthetic"] >= 19


def test_kept_copies_are_reimported_with_the_same_origin(repo, tmp_path):
    import_file(FIXTURE, repo, origin="public:https://example.org/pm", keep_dir=tmp_path)

    again = reimport_all(tmp_path, repo)

    assert [i.incident.origin for i in again] == ["public:https://example.org/pm"]


def test_an_import_cannot_overwrite_an_authored_case(repo, tmp_path):
    text = FIXTURE.read_text().replace("EXT-0001", "INC-1042")
    path = tmp_path / "clash.md"
    path.write_text(text)

    with pytest.raises(MarkdownImportError, match="authored corpus id"):
        import_file(path, repo)
