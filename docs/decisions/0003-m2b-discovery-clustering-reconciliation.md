# Decision 0003: M2B Recurring Correction Discovery, Independent Grouping, and Reconciliation

- **Status:** Accepted (M2B slice, protocol `0.1-draft` experimental)
- **Date:** 2026-11-28
- **Scope:** `src/onemind/discovery/`, `src/onemind/cli.py`, `examples/discovery-gold-corpus/`, `examples/discovery-heldout-corpus/`

## 1. Context

M2A ingests GitHub PR review history as immutable, inert `Evidence` records with explicit coverage accounting (`IngestionRun` manifests). M2B bridges raw review observations to **reviewable, inert `PracticeCandidate` proposals and `Conflict` findings** without introducing LLM requirements, violating non-interference, or conflating candidate discovery with human governance approval.

## 2. Decisions

### 2.1 Strict Three-Way Epistemic Separation

1. **Evidence** — Immutable observation of what a reviewer wrote and where/when (`source_id`, `source_uri`, `excerpt_sha256`).
2. **Candidate Interpretation (`PracticeCandidate` / `Conflict`)** — Deterministic, inspectable proposal derived from opted-in redacted evidence. May be incomplete, noisy, or wrong; remains strictly **inert**.
3. **Authorized Decision (`PracticeVersion` / `GovernanceDecision` / `Application`)** — Out of scope for M2B. Discovery and reconciliation **never** create, approve, suspend, retire, or alter any `PracticeVersion`, `GovernanceDecision`, active `Application`, or intervention `OutcomeEvent`.

### 2.2 Deterministic Baseline Extractor (Zero Mandatory LLM Dependency)

- Implemented `DeterministicReviewExtractor` in `src/onemind/discovery/extractor.py` implementing the existing `Extractor` protocol alongside a documented optional `OptionalModelExtractor` interface (no model SDK dependency).
- Operates strictly on locally stored, explicitly opted-in `redacted_snapshot` text. If evidence is `metadata-only` (snapshot omitted), extraction explicitly counts `missing_redacted_text` and marks coverage incomplete rather than silently fabricating certainty.
- Deduplicates superseded edit revisions before extraction so an edited comment is evaluated only once at its latest observed revision (while preserving prior immutable records in the ledger); `source-unavailable` / withdrawn comments are excluded from support.
- Filters bot chatter, compliments/approvals (`LGTM`), routine non-normative questions, withdrawn remarks, pure style/nit preferences (never promoted to safety rules), and hostile prompt-injection payloads.
- Assigns conservative descriptive category labels (`api-compatibility`, `security`, `validation`, `reliability`, `testing`, `maintainability`, `code-correctness`) explicitly tagged as labels, never proof of policy intent.
- Embeds generator provenance (`name`, `version`, `config_sha256`, `mode`) and explicit `uncertainty` (`level`, `reasons`, `incomplete`) in candidate metadata.

### 2.3 Independent PR Occurrence Counting & Scope-Aware Clustering

- **Counting unit:** Distinct `(repository, pr_number)` occurrences (not raw comment counts). Multiple comments within the same PR thread or across revisions of one PR count as **1** independent PR occurrence. Both `independent_pr_count` and `total_source_comments` are reported alongside `distinct_reviewers`, source timestamps, `path_concentration`, and `time_concentration`.
- **Cluster key:** Includes `(repository, scope_key, category, polarity, canonical_topic_key)`.
  - Opposite polarities (`require` vs. `forbid`, e.g., *"always use X"* vs. *"never use X"*) never merge into the same cluster; instead they are surfaced as contradictory clusters.
  - Incompatible condition qualifiers or divergent path scopes trigger `needs_manual_partitioning = true` rather than silent collapsing.
- **Support threshold:** Configurable `--min-independent-prs` (default `2`) is explicitly documented as a discovery heuristic, never organizational policy authorization.
- **Determinism:** Records are sorted canonically before extraction and clustering so shuffled input order yields identical cluster keys, candidate IDs, source links, counts, and scopes.

### 2.4 Scope-Aware Reconciliation & Non-Interference

- `reconcile_candidates()` compares candidate clusters against existing `PracticeCandidate` and `PracticeVersion` records into six explicit dispositions:
  - `identical`, `possible-duplicate`, `compatible-refinement`, `scope-overlap-review`, `contradictory`, `insufficient-context`.
- Emits valid `0.1-draft` `Conflict` records (`contradicts`, `scope-overlap`, `possible-exception`) with explicit `evidence_ids` and `detector` provenance.
- Never resolves ambiguity by recency, popularity, or score; never mutates existing approved practice state or effective projections.

### 2.5 Read-Only CLI Defaults & Standalone Review Digest

- `onemind discover`, `onemind reconcile`, and `onemind review-digest` are read-only by default.
- Persisting canonical `PracticeCandidate` or `Conflict` records requires explicit `--write` (atomic `append_many` after full `validate_records`).
- Writing a standalone proposal JSON or Markdown review digest to `--output <path>` explicitly reports the file side effect and never modifies `.onemind/`, creates a GitHub PR, or implies approval.
- Incomplete ingestion manifests (`complete: false`) or missing redacted snapshots propagate into explicit incomplete discovery status (exit code `3`).

## 3. Evaluation Discipline

Two synthetic offline corpora (`examples/discovery-gold-corpus/` and independent `examples/discovery-heldout-corpus/`) with separate `expected_labels.json` manifests measure detection, clustering, evidence linking, and scope precision/recall independently. Metrics apply solely to those fixed synthetic fixtures and support no empirical real-world quality claims.
