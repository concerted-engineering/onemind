# OneMind Architecture (M0 + M1 — Protocol `0.1-draft`, Experimental)

> **Your team learns once. Every agent benefits.**

OneMind is an open-source, local-first engineering-intelligence substrate for **evidence-backed practices, explicit governance, independently versioned applications, and observed outcomes**. It supports the Concerted Engineering lifecycle (**Discover → Define → Execute → Verify → Deliver → Learn**) without coupling to any particular AI model, coding agent, CI/CD platform, or hosted backend.

## 1. Architectural Layers

```text
+-----------------------------------------------------------------------+
| CLI (`onemind`: scan, inspect, propose, preview, validate)            |
+-----------------------------------------------------------------------+
| Logical Domain API (`OneMindEngine`: query, propose, resolve, observe)|
+----------------------------+------------------------------------------+
| Governance & Replay        | Composable Adapters & Built-in Checks    |
| - AuthorityPolicy          | - SourceAdapter, Extractor, Reporter     |
| - AuthorityVerifier        | - ApplicationAdapter, OutcomeSink        |
| - EffectiveView(c, t, p)   | - LocalFilesSourceAdapter, ApiCompatJson |
+----------------------------+------------------------------------------+
| Immutable Protocol & Storage (`0.1-draft` experimental)               |
| - 7 record kinds + JSON Schema 2020-12 + Semantic Validator           |
| - RFC 8785 JCS canonicalization + SHA-256 digests                     |
| - Git-backed append-only `.onemind/records/` & `.onemind/batches/`    |
| - Trusted-base historical mutation validation                         |
+-----------------------------------------------------------------------+
```

## 2. Seven Immutable Record Kinds

All records share a versioned JSON envelope (`schema_version: "0.1-draft"`, `record_id`, `kind`, `created_at`, `actor`, `body`):

1. **`evidence`** — Immutable observation of a local or external artifact (`source_uri`, `excerpt_sha256`, `source_state`, optional `redacted_snapshot`). Always inert.
2. **`practice_candidate`** — Source-linked normative suggestion with generator provenance. Always inert; never activates or routes.
3. **`practice_version`** — Versioned normative statement (`practice_id`, `version`, `scope`, `owner`, `evidence_ids`, `previous_version_id`). Contains no self-asserted approval state.
4. **`governance_decision`** — Immutable action (`approve`, `suspend`, `restore`, `retire`, `adjudicate`) bound to an exact `target_record_id` and its RFC 8785 canonical `target_sha256`, carrying `authority` and `proof`.
5. **`conflict`** — Recorded finding (`contradicts`, `scope-overlap`, `possible-exception`) between two candidates/practices. Never implicitly disables approved practices.
6. **`application`** — Separately versioned interpretation (`preflight`, `linter`, `test`, `agent-context`, `record-only`) whose `applicability` must be no wider than its governing `practice_version.scope`.
7. **`outcome_event`** — Deduplicated, append-only observation (`dedup_key`) recorded in immutable batch directories with SHA-256 manifests. Never mutates governance state.

## 3. Transport-Independent Logical API

Implemented in `src/onemind/engine.py` (`OneMindEngine`):

- `query(criteria, as_of)` — Read-only search returning records annotated with explicit authority class (`inert-evidence`, `inert-candidate`, `unapproved`, `local-owner-assertion`, `verified-repository-approval`) and lifecycle state.
- `propose(candidate, evidence=..., write=False)` — Validates and (only when `write=True`) persists inert evidence/candidate records. Never activates practices.
- `resolve(context, as_of)` — Projects `EffectiveView(c, t, p)` into an explicit resolution state (`none`, `active`, `ambiguous`, `suspended`, `indeterminate`) with provenance. Read-only lookup, never an execution grant.
- `observe(event, batch_id=None)` — Appends deduplicated `outcome_event` records to immutable batches without touching governance.

## 4. Deterministic Effective-State Projection

`project_effective_state()` in `src/onemind/governance/projection.py` computes `EffectiveView(c, t, p)` as a pure function of:

- Canonical records at commit `c` (validated structurally and semantically; broken integrity fails safely to `indeterminate`).
- Evaluation timestamp `t` (`as_of`), filtering decisions with `effective_at <= t`.
- External versioned `AuthorityPolicy` `p` (`.onemind/policy.json` or conservative default).
- Committed Git ledger sequence order (never using host clocks or `created_at` as authority tie-breakers).

Lifecycle transitions per target are deterministic (`unapproved → approved ↔ suspended → retired`, where `retired` is terminal). Suspending an application does not suspend its governing practice; retiring a practice invalidates all applications referencing it.

## 5. Adapter Composability & M2A Remote Ingestion

`src/onemind/adapters/interfaces.py` defines minimal `Protocol` interfaces (`SourceAdapter`, `RemoteSourceAdapter`, `Extractor`, `AuthorityVerifier`, `Store`, `ApplicationAdapter`, `OutcomeSink`, `Reporter`). Local/file adapters (`LocalFilesSourceAdapter`, `MarkdownRetroAdapter`, `JsonObservationAdapter`) implement `SourceAdapter.ingest(source_path, repository=...)`, while bounded remote API ingestion (`GitHubReviewSourceAdapter` in `src/onemind/adapters/github/`) implements `RemoteSourceAdapter.ingest_remote(request: SourceRequest)` without forcing fake local paths.

Run-level ingestion coverage for M2A is persisted as an append-only `IngestionRun` manifest under `.onemind/ingestions/<run_id>/manifest.json` (`0.1-draft-ingestion-run`), preserving the seven canonical `0.1-draft` record kinds without fabricating practice/application IDs in `OutcomeEvent`. Both `GitRecordStore.verify_ingestion_manifests()` and `verify_trusted_base()` validate persisted ingestion manifests against tampering and deletion. See [ADR-0002](decisions/0002-m2a-github-evidence-ingestion.md).
