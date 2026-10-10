# Historical Incident and Runbook Assistant

An operations engineer describes a current incident. The system searches a
labelled historical corpus and returns either a **cited investigation plan** or
an **escalation report** when the evidence is too weak, stale, or contradictory
to name a cause. It never applies a fix. See [info.md](info.md) for the brief.

The escalation path is a product rule, not a prompt suggestion. A normal RAG
chatbot will invent a root cause; this one is not allowed to, because the
decision is made in code before and after generation.

Everything runs locally. No request leaves the machine.

## What makes it more than document Q&A

Similar text does not mean similar incident, so retrieval scores six signals and
reports all of them next to the total:

| Signal | Why it is there |
| --- | --- |
| Semantic similarity | Finds cases that read like the symptoms |
| Metadata agreement | Service, environment, region, severity, symptom overlap |
| Timeline sequence | Did the incidents unfold in the same order |
| Timeline intervals | Were the gaps between events comparable |
| Recency | A four-year-old match should not beat a recent one |
| Service map | One-hop dependencies of the affected service |

On top of that: a hard environment filter (a staging case is the wrong case,
however well the words line up), a stale flag on cases that predate the current
stack, and conflicting-fix detection that reads structured remediation actions
rather than prose.

## Prerequisites

- Python 3.11+
- Node 18+
- [Ollama](https://ollama.com) running locally, with both models pulled:

```bash
ollama serve
ollama pull qwen2.5:7b        # chat and JSON extraction
ollama pull nomic-embed-text  # embeddings
```

Ollama rather than LM Studio because it is a headless HTTP service: the app can
start, health-check, and index a corpus without a desktop window open. On a
RAM-tight machine set `INCIDENT_CHAT_MODEL=qwen2.5:3b`; the code path is the
same. `LLMProvider` (`chat`, `embed`, `status`) is the only model surface the
rest of the app may touch, so pointing at a different OpenAI-compatible server
later is a config change plus one class, not a second integration.

## Running it

```bash
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

python -m app.cli seed      # load the authored corpus into SQLite
python -m app.cli ingest     # chunk, embed, and index into Chroma
uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend
npm install
npm run dev                  # http://127.0.0.1:5173, proxies /api to port 8000
```

`seed` and `ingest` are separate on purpose: starting the API only ensures the
schema, so it can never silently rewrite the knowledge the index was built from.
Investigating before indexing returns a 503 that says which command to run.

## Operator commands

```bash
python -m app.cli seed                     # load the corpus
python -m app.cli ingest                   # build the vector index
python -m app.cli stats                    # row counts per table
python -m app.cli retrieve CUR-2051        # retrieval and the gate, no model
python -m app.cli investigate CUR-2051     # the full pipeline
python -m app.cli eval                     # score against the labelled cases
python -m app.cli eval --with-generation   # also check citation validity (slow)
pytest                                     # 98 tests, no model required
```

`retrieve` exists because retrieval quality is the hard part of this project and
should be judgeable without the model in the way.

## How a request is decided

```text
current incident
      -> extract service, environment, symptoms, timeline
      -> hybrid retrieval (semantic arm + metadata arm)
      -> environment filter, reported rather than silent
      -> six-component rerank with recency and stale flags
      -> timeline alignment against each candidate
      -> conflicting-fix detection
      -> CONFIDENCE GATE
           |                        |
       sufficient              insufficient
           |                        |
   cited investigation      escalation report
```

The gate runs twice. Before generation it decides whether a cause may be named
at all. After generation it checks the output: citations naming ids nobody
retrieved are dropped, steps left with no support are dropped, and a cause
asserted on evidence the gate rejected is removed. If nothing survives, the
answer becomes an escalation report. The model's own confidence label is
advisory in both directions, so `undetermined` stays a statement about evidence
rather than a mood.

Every adjustment is recorded in `validation_notes` and shown in the UI.

## The corpus is also the eval set

19 synthetic incidents, 11 runbooks, 11 postmortems, log excerpts, and a service
map, all clearly labelled synthetic. Cases were authored to exercise specific
behaviour:

| Case | Role |
| --- | --- |
| `INC-1042` | The correct match for the walkthrough incident |
| `INC-1198` | Reverses INC-1042's pool change, for conflict detection |
| `INC-1120` / `INC-1320` | A second conflicting pair, on worker scaling |
| `INC-0995` | Same service and symptoms, but staging: must be excluded |
| `INC-0412` | Near-identical and four years old: must rank lower and flag stale |

Five labelled current incidents (`CUR-2051` to `CUR-2055`) carry expected
outcomes, including one that must escalate. `python -m app.cli eval` measures
recall@5, top-1, runbook@1, stale flagging, environment exclusion, conflict
identification, escalation behaviour, and citation validity, and exits non-zero
when any labelled expectation is missed.

## Layout

```
backend/
  app/
    config.py            Settings, including every scoring weight and threshold
    domain/              Dataclasses and enums; no framework imports
    db/                  schema.sql and the SQLite repository
    corpus/data/*.json   The authored corpus and the labelled eval cases
    ingest.py            Section-aware chunking, then embedding
    vectors.py           VectorStore seam: Chroma, plus in-memory for tests
    analysis/            temporal.py, conflicts.py, scoring.py, gate.py
    retrieval.py         Hybrid retrieval and the evidence pack
    services/            prompts.py, investigation.py, reports.py
    eval.py              Scored evaluation against the labels
    api/                 FastAPI routes
  tests/                 98 tests; a hashing fake stands in for Ollama
frontend/
  src/components/        Workspace, comparable cases, timelines, source viewer
```

The analysis modules are pure functions over dataclasses, which is why timeline
alignment and conflict detection can be asserted exactly rather than sampled.

## Scope

In: the workflow from the brief, end to end, with the advanced retrieval it
calls for.

Out, deliberately: packet captures, malware or file execution, live log
streaming, SIEM connectors, authentication, and any automatic remediation.

## Room for the later platform

The intended direction is a mini SOC assistant where a user uploads logs,
captures, suspicious files, and security documentation. The seams for that exist
now; nothing behind them does.

- A **case** is the unit of work. The current incident is its first artifact, and
  later uploads attach to the same case rather than to a second application.
- `ArtifactKind` already declares `pcap`, `file_sample`, and `security_doc`.
  Posting one returns **501 with an explanation**, so the boundary is visible in
  the API instead of implied by what is missing.
- Retrieval, the evidence pack, and the gate are already separate. A log,
  capture, or document analyzer would produce the same evidence fragments and
  feed the same gate.
- A file sample, when that analyzer exists, would be stored and described. Never
  executed. Remediation stays a recommendation a human reads.
