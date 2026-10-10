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
