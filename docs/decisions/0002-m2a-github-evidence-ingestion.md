# Decision 0002: M2A Read-Only GitHub Review-History Evidence Ingestion

- **Status:** Accepted (M2A slice, protocol `0.1-draft` experimental)
- **Date:** 2026-11-28
- **Scope:** `src/onemind/adapters/github/`, `src/onemind/storage/`, `src/onemind/cli.py`

## 1. Context

M2A introduces OneMind's first real-world external ingestion pipeline: reading GitHub pull-request review history (review submissions, inline review comments, and PR conversation comments) for a single user-selected repository over a bounded time window.

Three design tensions must be resolved cleanly without compromising M0/M1 invariants:

1. **Protocol mismatch for run-level coverage accounting:** In `spec/record.schema.json` (`0.1-draft`), `OutcomeEvent` (even for `event_type="run-health"`) requires `pr_id`, `head_sha`, `practice_version_id`, `application_record_id`, and `checker_version`. Repository-wide review ingestion runs *before* any practice or application exists. Inventing placeholder IDs would corrupt protocol semantics and violate cross-record referential integrity.
2. **Privacy and credential hygiene:** GitHub review comments frequently contain tokens, secrets, internal identifiers, emails, or hostile Markdown. Persisting raw review text by default or echoing secrets/bodies to terminal logs would create a data-exposure hazard.
3. **Pagination, edits, deletion, and completeness:** GitHub REST endpoints paginate via RFC 8288 `Link` headers, enforce primary and secondary rate limits, and allow in-place comment edits. Truncation, permission errors, or partial pagination must never be misreported as complete coverage or absence of review corrections.

## 2. Decisions

### 2.1 Data Flow & Separation of Concerns

```text
User CLI / API (`onemind ingest github`, dry-run default)
  │
  ▼
HttpTransport (injectable; stdlib urllib with strict origin/redirect/size/retry guards)
  │  raw GitHub REST JSON (PRs, reviews, inline review comments, issue comments)
  ▼
GitHubNormalizer (pure payload -> source-neutral 0.1-draft Evidence)
  │  + DataCapturePolicy & Deterministic Redactor (metadata-only default | redacted-excerpt opt-in)
  ▼
Deduplication & Edit Versioning (deterministic record_id from canonical observation fingerprint)
  │  unchanged -> skip; edited -> append new immutable Evidence; deleted -> only with reliable proof
  ▼
Atomic Persistence (only with --write):
  - `.onemind/records/evidence/<record_id>.json` (atomic append_many)
  - `.onemind/ingestions/<run_id>/manifest.json` (atomic staged IngestionRun manifest)
```

- Transport (`transport.py`), privacy/redaction (`policy.py`), payload normalization (`normalize.py`), coverage accounting (`coverage.py`), and orchestration (`adapter.py`) are isolated modules.
- Existing `SourceAdapter.ingest(source_path: Path, *, repository: str)` remains untouched for local/file adapters (`LocalFilesSourceAdapter`, `MarkdownRetroAdapter`, `JsonObservationAdapter`). A compatible `RemoteSourceAdapter` protocol with `SourceRequest` handles bounded remote queries without fabricating fake `Path` arguments.
- Zero external GitHub SDK, LLM, or agent framework dependencies are added (stdlib `urllib.request` + existing `jsonschema`).

### 2.2 Run-Level Coverage Accounting (`IngestionRun` Auxiliary Store)

Instead of mutating `spec/record.schema.json` or inserting fake practice/application IDs into `OutcomeEvent`, M2A persists run-level coverage as an immutable, append-only **`IngestionRun` manifest** under `.onemind/ingestions/<run_id>/manifest.json` (`schema_version: "0.1-draft-ingestion-run"`).

- **Why this choice:**
  - Preserves all 7 `0.1-draft` record kinds and existing M0/M1 conformance suites with zero breaking schema changes.
  - Records exact query/window parameters (`repository`, `api_origin`, `since`, `until`, `states`, `max_prs`, `max_pages`, `capture_policy`, `authenticated`), adapter version, coverage counters, per-endpoint failure/truncation gaps, oldest/newest source timestamps, `complete` boolean, `incomplete_reasons`, and the exact `(record_id, record_sha256)` list of persisted `Evidence` records (never raw bodies or tokens).
  - Integrated directly into `GitRecordStore.verify_ingestion_manifests()` and `verify_trusted_base()` so `onemind validate --trusted-base <ref>` detects any unauthorized modification or deletion of persisted ingestion manifests alongside `.onemind/records/` and `.onemind/batches/`.
- **Future migration path:** If a future protocol version (`0.2`) introduces repository-scoped `run_health` records without practice/application bindings, `IngestionRun` manifests map 1-to-1 into that record kind without rewriting existing immutable `Evidence` records.

### 2.3 Privacy Policy (`DataCapturePolicy`)

- **`metadata-only` (DEFAULT):** Never writes `redacted_snapshot` to disk or console. Computes `excerpt_sha256` from the normalized source text so edits remain deterministically detectable while omitting raw content (`redaction.snapshot_omitted = true`).
- **`redacted-excerpt` (explicit opt-in via `--capture-policy redacted-excerpt`):** Applies deterministic regex-based redaction (GitHub tokens, AWS keys, Bearer/Basic credentials, PEM private keys, generic API keys/passwords, email addresses, ANSI/control sequences) and length truncation (`max_excerpt_chars`, default 500), recording redaction provenance (`redaction` object) and openly documenting that heuristic redaction cannot guarantee 100% PII/secret removal.
- **Credential safety:** Tokens are read only from an environment variable named by `--token-env` (never accepted as a CLI argument), never written to manifests or records, and scrubbed from all exception messages and diagnostics. Public repositories work without a token when GitHub unauthenticated rate limits permit.

### 2.4 Idempotency, Edit Versioning, and Unavailability

- Each observed item computes a deterministic fingerprint over `(repository, source_type, source_id, source_revision, content_digest)` and derives `record_id = "gh-ev-" + fingerprint[:24]`.
- **Unchanged re-ingestion:** Produces the identical `record_id` regardless of page boundaries or API traversal order; skipped as `unchanged_deduplicated` (zero duplicates).
- **Edited comment/review:** Updated `updated_at` or `content_digest` yields a new `record_id`, appending a new immutable `Evidence` record (`extensions.github.supersedes_evidence_id` linking to the prior observation) while preserving the prior immutable record intact.
- **Deleted / unavailable items:** `source_state: "source-unavailable"` is appended **only** when there is reliable evidence of unavailability (such as an explicit HTTP `404`/`410` on a specifically queried item ID or an explicit deleted marker), never inferred from a truncated or partially fetched list.
- **Distinct timestamps:** Source timestamps (`source_created_at`, `source_updated_at`) are stored distinctly from `observed_at` and `Evidence.created_at`.

### 2.5 Trust Boundaries & Coverage Semantics

- Every ingested GitHub item is strictly **inert `Evidence`**. Review states (`APPROVED`, `CHANGES_REQUESTED`) are descriptive source metadata only and NEVER create `PracticeVersion`, `Application`, or `GovernanceDecision` records.
- HTTP transport enforces a single explicitly configured trusted HTTPS origin (default `https://api.github.com`), blocks cross-origin/scheme-downgrade redirects and malicious `Link` headers, detects pagination loops, caps response payload sizes, validates JSON content types, and retries only bounded transient errors (`429`, `5xx`, rate-limit `403`, timeouts) with injectable clock/sleep.
- Any truncation (`--max-prs`, `--max-pages`), pagination loop, malformed payload, auth/permission error (`401`/`403`), or retry exhaustion sets `complete = false`, records an explicit gap, and returns CLI exit code `3` (`EXIT_INDETERMINATE`).

## 3. Consequences

- M2A delivers an auditable, offline-testable, privacy-safe foundation for real GitHub PR evidence ingestion.
- M2B (verified reviewer/protected-merge authority proofs, trusted preflight execution, and experiment assignment) remains cleanly deferred and tracked in `docs/M2_BLOCKERS.md`.
