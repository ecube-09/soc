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

On top of that:

- **Environment is a hard filter.** A staging case is the wrong case, however
  well the words line up. Excluded cases are listed, not hidden.
- **Region is a preference, not a filter** (`INCIDENT_REGION_POLICY=prefer`).
  A Redis pool exhausting in `eu-west-1` is still the same failure in
  `ap-south-1`, so other-region cases stay in the list with an `other-region`
  flag and a "different region" reason. Set `require` and they move into the
  excluded list instead, labelled `region`, exactly like the staging exclusion.
- **Stale cases are flagged.** Their reasoning may transfer; their remediation
  values probably do not.
- **Conflicting fixes are detected** from structured remediation actions, not
  prose, and shown once, in their own panel, with the measurement that decides
  between them.
- **Support means the same failure, not the same service.** A case only counts
  towards the hypothesis if it scores near the top match *and* shares its
  failure shape: an implicated dependency and a similar event sequence. Another
  payment-api incident caused by a dropped index is a neighbour, not support.

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
start, health-check, and index a corpus without a desktop window open.
`LLMProvider` (`chat`, `embed`, `status`) is the only model surface the rest of
the app may touch, so pointing at a different server later is a config change
plus one class, not a second integration.

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
`seed` keeps saved cases and re-imports any Markdown postmortems you imported.

## Operator commands

```bash
python -m app.cli seed                       # load the corpus (keeps cases)
python -m app.cli ingest                     # build the vector index
python -m app.cli stats                      # row counts per table
python -m app.cli retrieve CUR-2051          # retrieval and the gate, no model
python -m app.cli investigate CUR-2051       # the full pipeline
python -m app.cli eval                       # score against the labelled cases
python -m app.cli eval --sensitivity         # ...and nudge every weight (fast)
python -m app.cli eval --with-generation     # ...and check the model's output (slow)
python -m app.cli import-md pm.md --origin public:https://example.org/pm
pytest                                       # 153 tests, no model required
```

`retrieve` exists because retrieval quality is the hard part of this project and
should be judgeable without the model in the way.

## How a request is decided

```text
current incident (+ notes, log lines, recorded measurements on the case)
      -> hybrid retrieval (semantic arm + metadata arm)
      -> environment filter, region policy; both reported, not silent
      -> six-component rerank with recency and stale flags
      -> timeline alignment against each candidate
      -> conflicting-fix detection, settled by any recorded measurement
      -> CONFIDENCE GATE
           |                        |
       sufficient              insufficient
           |                        |
   cited investigation      escalation report
```

The gate runs twice. Before generation it decides whether a cause may be named
at all. After generation it checks what the model wrote:

| Check | What happens |
| --- | --- |
| Citation was not retrieved | Citation dropped; a step left with none is dropped |
| Cited document does not contain the claim | Citation dropped (stemmed word coverage under 20%) |
| Step names no historical case | Dropped; runbooks are shown in full on their own |
| Step restates a runbook line | Dropped, with the runbook named |
| Step proposes moving a disputed setting | Dropped while the conflict is open |
| Step moves a setting the way the ruled-out side did, or rests only on that side | Dropped once a measurement settles the conflict |
| More steps than the cap (`INCIDENT_MAX_STEPS`, default 4) | Case-backed steps kept first |
| Hypothesis is not grounded in a supporting case | Removed; the answer becomes an escalation |
| Hypothesis names no case | Attributed to the case it matches, preferring the top match |
| Hypothesis contains a fix | The fix sentence is cut |
| Caution or unresolved point repeats the conflict | Removed; it is stated once, in the conflict panel |
| Headline is a title ("Incident Analysis and…") | Replaced with a sentence built from the evidence |

If no step survives, the model is shown the reasons and asked once more; if the
second draft fails too, the answer becomes an escalation and the screen says the
evidence allowed an investigation but the draft did not. The model's own
confidence label is advisory in both directions, so `undetermined` stays a
statement about evidence rather than a mood. Every adjustment is recorded in
`validation_notes` and shown in the UI.

The JSON schema sent to Ollama restricts each step's `cases` field to the ids
that were retrieved, so a step cannot be produced without a real case behind
it. The checks above still decide whether that case says what the step claims.

## Working a case

A **case** is the unit of work, and each run is saved on it as a numbered
revision: the evidence pack, the hypothesis or escalation report, and the
validation notes.

- **Second pass.** Attach notes or log lines you gathered, then re-run. The
  revision banner says what changed: "1 finding added. Confidence went from 75%
  to 90%. Conflict settled in favour of INC-1042." The gate is free to move in
  either direction.
- **Recorded measurements.** In the conflict panel, pick the side your
  measurement matches and write what you saw. A measurement for the top match
  removes the conflict penalty; one against it multiplies confidence by 0.4
  (`INCIDENT_CONTRADICTED_TOP_FACTOR`) and usually turns the answer into an
  escalation. The latest measurement per setting wins.
- **Timeline from logs.** "Propose timeline events from the log" runs rules over
  the pasted lines and proposes `deploy`, `error_spike`, `latency_spike`,
  `dependency_timeout`, `alert`, and `mitigation` events, each with the line it
  came from. Nothing joins the timeline until you tick it, because a mis-typed
  event changes the ranking.
- **Who and when.** Every case, artifact, and revision records the investigator.
  Two clocks are kept apart: the incident's own time, which recency is measured
  against, and the wall clock when the revision ran. Replaying an old incident
  next month gives the same ranking.
- **Export.** Copy or download any revision as Markdown
  (`GET /api/v1/cases/{id}/report?format=markdown|text&revision=N`). The header
  carries the case, revision, investigator, both clocks, and any recorded
  measurement; the conflict appears once.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/investigations` | Start a case and run revision 1 |
| `GET /api/v1/cases`, `GET /api/v1/cases/{id}` | Recent cases; one case with artifacts and revisions |
| `POST /api/v1/cases/{id}/artifacts` | Attach `note`, `log_excerpt`, or `finding` |
| `POST /api/v1/cases/{id}/investigations` | Re-run with everything attached |
| `GET /api/v1/cases/{id}/investigations/{n}` | A stored revision, unchanged |
| `GET /api/v1/cases/{id}/report` | Markdown or text export |
| `POST /api/v1/timeline/extract` | Proposed events from log text |

## The corpus is also the eval set

20 synthetic incidents, 11 runbooks, 11 postmortems, log excerpts, and a service
map, all labelled `origin: synthetic`. Cases were authored to exercise specific
behaviour:

| Case | Role |
| --- | --- |
| `INC-1042` | The correct match for the walkthrough incident |
| `INC-1198` | Reverses INC-1042's pool change, for conflict detection |
| `INC-1120` / `INC-1320` | A second conflicting pair, on worker scaling |
| `INC-0995` | Same service and symptoms, but staging: must be excluded |
| `INC-0412` | Near-identical and four years old: must rank lower and flag stale |
| `INC-1338` | Same failure in `eu-west-1`: kept and flagged under `prefer`, excluded under `require` |

Five labelled current incidents (`CUR-2051` to `CUR-2055`) carry expected
outcomes, including one that must escalate. `python -m app.cli eval` measures
recall@5, top-1, runbook@1, stale flagging, environment exclusion, conflict
identification, escalation behaviour, and citation validity, and exits non-zero
when any labelled expectation is missed.

### Read the eval numbers for what they are

The weights, threshold (0.45), recency half-life (120 days), and stale cutoff
(540 days) were **set by hand** so this corpus behaves. The eval prints that
first. A 5/5 shows the pipeline does what it was built to do on the cases it was
built around; it is not an accuracy estimate for incidents nobody wrote.

`eval --sensitivity` nudges each weight ±25%, the threshold ±0.05, the half-life
×0.5/×2, the stale cutoff ±25%, and the conflict penalty ×0.5/×2, one at a time,
and re-runs every labelled case. With the current models, 16 of 20 nudges change
nothing. The other four only swap the closest case for `CUR-2052`, whose top two
are 0.001 apart; that case escalates either way and asserts no closest case, so
no label fails. Top-1 margins on the other cases range from 0.06 to 0.21.

### What depends on the model

Retrieval, the gate, the mode, and the confidence are computed in code; the
model only drafts wording. But the ranking depends on `nomic-embed-text`, and
the hashing embedder used in tests does not prove another embedding model keeps
it. The drafts depend on `qwen2.5:7b`: on real runs it writes title-style
headlines, cites runbooks instead of cases, and paraphrases settings ("the
client pool" for `redis_connection_pool_size`), each of which the checks above
now catch because a real run surfaced it. With the schema constraint in place,
`eval --with-generation` passes 5/5 with every surviving step citing a past
case, though `CUR-2055` keeps only one step. Change either model and re-run it
before trusting the numbers.

### Limits of the checks

The faithfulness checks are lexical. They confirm the cited document shares the
step's vocabulary and that a step does not move a disputed setting; they do not
understand the claim. A paraphrase with no shared words can slip through, and a
log line that contradicts the hypothesis (`connected_clients=418 maxclients=1500`
against a maxclients story) is not read as a contradiction until an engineer
records it as a measurement. That is the point of the findings loop.

## Importing outside postmortems

```bash
python -m app.cli import-md path/to/postmortem.md --origin public:https://example.org/pm
python -m app.cli ingest
```

A Markdown postmortem with front matter (`incident_id`, `title`, `service`,
`environment`, `severity`, `started_at`, `origin`, ...) and `##` sections
(Summary, Symptoms, Timeline, Root cause, Resolution, Actions, Impact, Lessons
learned) fills the same incident, event, action, and postmortem tables. Timeline
lines may be typed (`10:15 deploy redis-cache: ...`) or raw log lines, which the
same extraction rules classify. Actions use the corpus form
(`rollback redis_version on redis-cache 7.2 -> 7.0`). Lines that cannot be
parsed are reported, not guessed. See `backend/tests/fixtures/sample_postmortem.md`.

- `origin` is required and `synthetic` is refused, so outside text is always
  labelled where it is shown.
- The eval only scores against `origin: synthetic`, so an import can change what
  an engineer sees but never a reported number.
- An import cannot overwrite an authored case id. Use `EXT-` ids.
- Copies are kept in `backend/data/imported/`, and `seed` re-imports them.

PDF is not supported. Converting a PDF to Markdown first keeps the parsing
honest and the original reviewable.

## Layout

```
backend/
  app/
    config.py            Settings: every weight, threshold, and policy
    domain/              Dataclasses and enums; no framework imports
    db/                  schema.sql and the SQLite repository (cases, revisions)
    corpus/data/*.json   The authored corpus and the labelled eval cases
    ingest.py            Section-aware chunking, then embedding
    ingest_markdown.py   Markdown postmortem import, labelled by origin
    vectors.py           VectorStore seam: Chroma, plus in-memory for tests
    analysis/            temporal, conflicts, scoring, gate, text, log_events
    retrieval.py         Hybrid retrieval and the evidence pack
    services/            prompts, investigation, faithfulness, reports, cases
    eval.py              Scored evaluation against the labels
    sensitivity.py       One-at-a-time weight perturbation
    api/                 FastAPI routes
  tests/                 153 tests; a hashing fake stands in for Ollama
frontend/
  src/components/        Form, case bar, recommendation, conflict panel with
                         measurements, evidence and re-run, comparable cases,
                         timeline proposals, source viewer
```

The analysis modules are pure functions over dataclasses, which is why timeline
alignment, conflict detection, and the faithfulness checks can be asserted
exactly rather than sampled.

## Scope

In: the workflow from the brief, end to end, with the advanced retrieval it
calls for, saved on a case that can be revisited and re-run.

Out, deliberately: a live log tail, packet-capture parsing, malware or file
execution, SIEM connectors, authentication, and any automatic remediation. Those
would change the product from an assistant that shows its evidence into a
system that acts.

## Room for the later platform

The intended direction is a mini SOC assistant where a user uploads logs,
captures, suspicious files, and security documentation.

- Cases, artifacts, and revisions exist now. Notes, log excerpts, and recorded
  measurements already attach to a case and feed the next run.
- `ArtifactKind` declares `pcap`, `file_sample`, and `security_doc`. Posting one
  returns **501 with an explanation**, so the boundary is visible in the API
  instead of implied by what is missing.
- Retrieval, the evidence pack, and the gate are separate. A capture or document
  analyzer would produce the same evidence fragments and feed the same gate.
- A file sample, when that analyzer exists, would be stored and described. Never
  executed. Remediation stays a recommendation a human reads.
