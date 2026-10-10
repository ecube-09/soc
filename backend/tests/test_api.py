import json

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_provider, get_repository, get_vector_store
from app.ingest import build_documents, index_documents
from app.main import create_app
from app.vectors import InMemoryVectorStore

DRAFT = json.dumps(
    {
        "headline": "payment-api looks like INC-1042.",
        "root_cause_status": "hypothesis",
        "root_cause_hypothesis": "Redis client pool exhaustion after the deploy.",
        "steps": [
            {
                "action": "Read the client-side pool metrics per replica.",
                "rationale": "Separates app-side exhaustion from saturation.",
                "citations": ["INC-1042", "RB-17", "INC-0000"],
            }
        ],
        "cautions": ["Pool size guidance conflicts."],
        "unresolved": ["Aggregate utilisation unmeasured."],
        "requested_team": "payments",
    }
)


@pytest.fixture
async def store(repo, provider):
    vector_store = InMemoryVectorStore()
    await index_documents(build_documents(repo), provider=provider, store=vector_store)
    return vector_store


@pytest.fixture
def client(settings, repo, store, provider):
    app = create_app(settings)
    app.dependency_overrides[get_provider] = lambda: provider
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_vector_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client


def incident_payload(case: str, client: TestClient) -> dict:
    examples = {e["case_id"]: e for e in client.get("/api/v1/examples").json()}
    return examples[case]["incident"]


def test_examples_expose_every_labelled_case(client):
    body = client.get("/api/v1/examples").json()

    assert {e["case_id"] for e in body} >= {"CUR-2051", "CUR-2052"}
    strong = next(e for e in body if e["case_id"] == "CUR-2051")
    assert strong["incident"]["service"] == "payment-api"
    assert len(strong["incident"]["events"]) == 5


def test_services_endpoint_returns_the_service_map(client):
    services = {s["name"]: s for s in client.get("/api/v1/services").json()}

    assert "redis-cache" in services["payment-api"]["dependencies"]
    assert services["payment-api"]["team"] == "payments"


def test_stats_reports_the_reserved_artifact_kinds(client):
    body = client.get("/api/v1/stats").json()

    assert body["counts"]["incidents"] >= 15
    assert body["indexed_chunks"] > 0
    assert set(body["reserved_artifact_kinds"]) == {
        "pcap",
        "file_sample",
        "security_doc",
    }


def test_investigation_returns_cited_steps_and_full_evidence(client, provider):
    provider.responses = [DRAFT]

    body = client.post(
        "/api/v1/investigations",
        json={"incident": incident_payload("CUR-2051", client)},
    ).json()

    assert body["mode"] == "investigation"
    assert body["root_cause_status"] == "hypothesis"
    assert body["case_id"].startswith("CASE-")
    assert body["steps"][0]["citations"] == ["INC-1042", "RB-17"]
    assert any("INC-0000" in note for note in body["validation_notes"])

    evidence = body["evidence"]
    assert evidence["candidates"][0]["incident_id"] == "INC-1042"
    assert evidence["candidates"][0]["timeline"]["matched"]
    assert evidence["conflicts"][0]["target"] == "redis_connection_pool_size"
    assert any(e["incident_id"] == "INC-0995" for e in evidence["excluded"])
    assert body["rendered"]


def test_weak_incident_returns_an_escalation_report(client, provider):
    provider.responses = [DRAFT]

    body = client.post(
        "/api/v1/investigations",
        json={"incident": incident_payload("CUR-2052", client)},
    ).json()

    assert body["mode"] == "escalation"
    assert body["root_cause_status"] == "undetermined"
    assert body["root_cause_hypothesis"] is None
    report = body["escalation_report"]
    assert report["service"] == "reporting-api"
    assert report["requested_team"]
    assert report["recommended_next_investigation"]
    assert "No remediation has been applied." in body["rendered"]


def test_every_citation_resolves_to_a_readable_source(client, provider):
    provider.responses = [DRAFT]

    body = client.post(
        "/api/v1/investigations",
        json={"incident": incident_payload("CUR-2051", client)},
    ).json()

    for source_id in body["evidence"]["citation_ids"]:
        response = client.get(f"/api/v1/sources/{source_id}")
        assert response.status_code == 200, source_id
        assert response.json()["body"]


def test_investigation_attaches_the_incident_to_a_case(client, provider):
    provider.responses = [DRAFT]

    case_id = client.post(
        "/api/v1/investigations",
        json={"incident": incident_payload("CUR-2051", client)},
    ).json()["case_id"]

    artifacts = client.get(f"/api/v1/cases/{case_id}/artifacts").json()
    assert [a["kind"] for a in artifacts] == ["incident"]
    assert artifacts[0]["payload"]["service"] == "payment-api"


def test_a_second_artifact_joins_the_same_case(client, provider):
    provider.responses = [DRAFT]

    case_id = client.post(
        "/api/v1/investigations",
        json={"incident": incident_payload("CUR-2051", client)},
    ).json()["case_id"]

    created = client.post(
        f"/api/v1/cases/{case_id}/artifacts",
        json={"kind": "log_excerpt", "payload": {"lines": ["timeout"]}},
    )

    assert created.status_code == 201
    assert [a["kind"] for a in client.get(f"/api/v1/cases/{case_id}/artifacts").json()] == [
        "incident",
        "log_excerpt",
    ]


@pytest.mark.parametrize("kind", ["pcap", "file_sample", "security_doc"])
def test_reserved_artifact_kinds_are_refused_with_an_explanation(client, kind):
    response = client.post("/api/v1/cases/CASE-X/artifacts", json={"kind": kind})

    assert response.status_code == 501
    detail = response.json()["detail"]
    assert kind in detail
    assert "never executed" in detail


def test_unknown_artifact_kind_is_a_validation_error(client):
    response = client.post("/api/v1/cases/CASE-X/artifacts", json={"kind": "nonsense"})

    assert response.status_code == 422
    assert "Known kinds" in response.json()["detail"]


def start_case(client, provider, case="CUR-2051", author="asha") -> dict:
    provider.responses = [DRAFT]
    return client.post(
        "/api/v1/investigations",
        json={"incident": incident_payload(case, client), "author": author},
    ).json()


def finding_for(favours: str) -> dict:
    return {
        "kind": "finding",
        "author": "ravi",
        "payload": {
            "target": "redis_connection_pool_size",
            "component": "payment-api",
            "favours_incident_id": favours,
            "observation": "pool=50/50 on every replica, Redis connected_clients 418/1500",
        },
    }


def test_the_investigation_is_saved_on_the_case(client, provider):
    first = start_case(client, provider)

    stored = client.get(f"/api/v1/cases/{first['case_id']}/investigations/1").json()
    assert stored["revision"] == 1
    assert stored["author"] == "asha"
    assert stored["evidence"]["candidates"][0]["incident_id"] == "INC-1042"
    assert stored["validation_notes"] == first["validation_notes"]
    assert stored["rendered_markdown"].startswith("# Investigation:")


def test_case_keeps_author_and_incident_time_apart_from_the_wall_clock(client, provider):
    first = start_case(client, provider)

    case = client.get(f"/api/v1/cases/{first['case_id']}").json()
    assert case["created_by"] == "asha"
    assert case["incident_started_at"].startswith("2026-09-27T14:09")
    assert not case["created_at"].startswith("2026-09-27")
    assert first["reference_time"] == "2026-09-27T14:09"
    assert [r["revision"] for r in case["revisions"]] == [1]


def test_a_finding_against_the_top_case_moves_the_rerun_to_escalation(client, provider):
    first = start_case(client, provider)
    case_id = first["case_id"]
    assert first["mode"] == "investigation"

    created = client.post(f"/api/v1/cases/{case_id}/artifacts", json=finding_for("INC-1198"))
    assert created.status_code == 201

    provider.responses = [DRAFT]
    second = client.post(
        f"/api/v1/cases/{case_id}/investigations", json={"author": "ravi"}
    ).json()

    assert second["revision"] == 2
    assert second["mode"] == "escalation"
    assert second["previous"]["mode"] == "investigation"
    assert "Moved from investigation to escalation." in second["changes"]
    assert any("1 finding(s) added" in c for c in second["changes"])
    resolution = second["evidence"]["conflicts"][0]["resolution"]
    assert resolution["favours_incident_id"] == "INC-1198"
    assert resolution["author"] == "ravi"


def test_a_later_finding_for_the_top_case_moves_it_back(client, provider):
    case_id = start_case(client, provider)["case_id"]
    client.post(f"/api/v1/cases/{case_id}/artifacts", json=finding_for("INC-1198"))
    provider.responses = [DRAFT]
    client.post(f"/api/v1/cases/{case_id}/investigations", json={})

    client.post(f"/api/v1/cases/{case_id}/artifacts", json=finding_for("INC-1042"))
    provider.responses = [DRAFT]
    third = client.post(f"/api/v1/cases/{case_id}/investigations", json={}).json()

    assert third["revision"] == 3
    assert third["mode"] == "investigation"
    assert third["confidence"] == pytest.approx(
        third["evidence"]["candidates"][0]["score"], abs=0.001
    )
    assert "Conflict on redis_connection_pool_size settled in favour of INC-1042." in third["changes"]


def test_notes_and_logs_reach_the_rerun(client, provider):
    case_id = start_case(client, provider)["case_id"]
    client.post(
        f"/api/v1/cases/{case_id}/artifacts",
        json={"kind": "note", "payload": {"text": "Autoscaler added 4 replicas at 14:05."}},
    )
    client.post(
        f"/api/v1/cases/{case_id}/artifacts",
        json={"kind": "log_excerpt", "payload": {"text": "14:20:01 WARN pool=50/50 waiters=80"}},
    )

    provider.responses = [DRAFT]
    second = client.post(f"/api/v1/cases/{case_id}/investigations", json={}).json()

    current = second["evidence"]["current_incident"]
    assert current["notes"] == ["Autoscaler added 4 replicas at 14:05."]
    assert "waiters=80" in current["log_excerpt"]
    assert "Autoscaler added 4 replicas" in provider.prompts[-1][1].content


def test_report_exports_as_markdown_and_text(client, provider):
    case_id = start_case(client, provider)["case_id"]

    markdown = client.get(f"/api/v1/cases/{case_id}/report")
    text = client.get(f"/api/v1/cases/{case_id}/report?format=text")

    assert markdown.status_code == 200
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert f'filename="{case_id}-r1-investigation.md"' in markdown.headers["content-disposition"]
    assert "**Investigator:** asha" in markdown.text
    assert "Nothing here has been applied" in markdown.text
    assert text.text.startswith("payment-api looks like INC-1042.")


def test_escalation_report_export_states_the_conflict_once(client, provider):
    case_id = start_case(client, provider)["case_id"]
    client.post(f"/api/v1/cases/{case_id}/artifacts", json=finding_for("INC-1198"))
    provider.responses = [DRAFT]
    client.post(f"/api/v1/cases/{case_id}/investigations", json={})

    report = client.get(f"/api/v1/cases/{case_id}/report").text

    assert report.startswith("# Escalation:")
    assert report.count("INC-1042 applied increase redis_connection_pool_size") == 1
    assert "Recorded measurement:" in report


def test_cases_are_listed_with_their_latest_revision(client, provider):
    case_id = start_case(client, provider)["case_id"]

    listed = {c["case_id"]: c for c in client.get("/api/v1/cases").json()}
    assert listed[case_id]["latest"]["revision"] == 1
    assert listed[case_id]["latest"]["top_incident_id"] == "INC-1042"


def test_rerun_of_an_unknown_case_is_404(client):
    response = client.post("/api/v1/cases/CASE-NOPE/investigations", json={})
    assert response.status_code == 404


@pytest.mark.parametrize(
    "body, message",
    [
        ({"kind": "note", "payload": {}}, "needs some text"),
        ({"kind": "log_excerpt", "payload": {}}, "needs `text` or `lines`"),
        ({"kind": "finding", "payload": {"target": "x"}}, "A finding needs"),
    ],
)
def test_artifact_payloads_are_validated(client, body, message):
    response = client.post("/api/v1/cases/CASE-X/artifacts", json=body)

    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_timeline_extraction_proposes_typed_events(client):
    body = client.post(
        "/api/v1/timeline/extract",
        json={
            "service": "payment-api",
            "date": "2026-09-27",
            "log_text": (
                "14:02:00 INFO deploy payment-api v2.8.1 rolled out\n"
                "14:14:02 ERROR redis.pool TimeoutError: timeout waiting for connection from pool\n"
                "14:14:20 INFO redis.stats server_ok=true\n"
                "no timestamp on this line"
            ),
        },
    ).json()

    assert [(e["type"], e["service"]) for e in body["events"]] == [
        ("deploy", "payment-api"),
        ("dependency_timeout", "redis-cache"),
    ]
    assert body["events"][0]["at"] == "2026-09-27T14:02"
    assert body["lines_without_time"] == 1
    assert body["lines_unclassified"] == 1


def test_investigation_requires_a_service_and_title(client):
    response = client.post("/api/v1/investigations", json={"incident": {"title": ""}})

    assert response.status_code == 422


def test_investigation_reports_an_unindexed_corpus_instead_of_guessing(
    settings, repo, provider
):
    app = create_app(settings)
    app.dependency_overrides[get_provider] = lambda: provider
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_vector_store] = lambda: InMemoryVectorStore()

    with TestClient(app) as empty_client:
        response = empty_client.post(
            "/api/v1/investigations",
            json={
                "incident": {
                    "title": "Something is wrong",
                    "service": "payment-api",
                    "environment": "production",
                    "severity": "SEV-2",
                }
            },
        )

    assert response.status_code == 503
    assert "app.cli ingest" in response.json()["detail"]
