# M2 Blockers & Prerequisites

**Status:** **M2A** (read-only GitHub PR review-history evidence ingestion and run-level coverage accounting) and **M2B** (deterministic recurring-correction discovery, independent-PR grouping, scope-aware reconciliation, inert conflict reporting, and human review digest) are implemented and offline-tested. **M2C** (verified GitHub governance proofs, privileged publisher separation, advisory PR preflight, held-out detector evaluation) and **M2D** (preregistered prospective outcome measurement) remain **explicitly deferred**. No empirical claims about engineering improvement may be made from synthetic fixtures or historical observations.

Before enabling verified governance promotion, active preflight, or prospective experimentation in M2C/M2D, every remaining prerequisite below must be designed, security-reviewed, and backed by automated conformance tests.

## 1. Real GitHub Evidence Ingestion, Coverage Accounting & Candidate Discovery — **Resolved (M2A + M2B)**

- **M2A:** Implemented read-only [`GitHubReviewSourceAdapter`](../src/onemind/adapters/github/adapter.py) (`onemind ingest github`) over an injectable origin-constrained stdlib HTTPS transport, supporting RFC 8288 `Link` pagination, loop/truncation detection, bounded retries with injectable clock/sleep, deterministic edit/deduplication versioning, explicit `DataCapturePolicy` (`metadata-only` default; opt-in `redacted-excerpt`), and immutable `IngestionRun` coverage manifests (`.onemind/ingestions/<run_id>/manifest.json`, verified under `--trusted-base`; [ADR-0002](decisions/0002-m2a-github-evidence-ingestion.md)).
- **M2B:** Implemented zero-LLM [`DeterministicReviewExtractor`](../src/onemind/discovery/extractor.py), independent-PR occurrence grouping ([`discover_recurring_corrections`](../src/onemind/discovery/clustering.py)), 6-way scope-aware non-interfering reconciliation ([`reconcile_candidates`](../src/onemind/discovery/reconcile.py)), read-only Markdown review digests ([`render_review_digest_markdown`](../src/onemind/discovery/digest.py)), CLI commands (`discover`, `reconcile`, `review-digest`), and synthetic gold + independent held-out corpus evaluation ([ADR-0003](decisions/0003-m2b-discovery-clustering-reconciliation.md)).
- *Remaining live operational note:* Live GitHub API ingestion requires explicit user opt-in; automated test suites remain 100% offline.

## 2. Verified Reviewer Authority and Protected-Merge Checks (M2C — Open)

- Replace local proof-structure checks with a live `AuthorityVerifier` anchored to GitHub's protected-branch rulesets, CODEOWNERS / qualifying reviewer lists, and exact review-commit-to-merge-commit binding.
- Verify that the approved content's RFC 8785 canonical SHA-256 digest matches the exact commit reviewed by a qualifying human reviewer and merged through branch protection (rejecting post-approval pushes, self-approvals where disallowed, and bypassed merges).
- Re-verify post-merge against a separately fetched trusted base commit so PR branches cannot tamper with `.onemind/policy.json` or historical records.

## 3. Trusted Workflow and Publisher Separation

- Enforce strict GitHub Actions privilege separation: untrusted PR code (especially from forks) must **never** execute inside workflows holding write tokens, secrets, or ledger-commit permissions.
- Evaluate preflight checks using trusted base-branch code reading PR diffs strictly as data, and isolate PR comment publication / immutable batch writes into a restricted publisher job with narrow permissions.
- Avoid `GITHUB_TOKEN`-triggered recursive workflow chains; use explicit, auditable verification triggers for bot-committed immutable event batches.

## 4. Held-Out Detector Evaluation

- Establish a held-out set of historical pull requests (strictly separated from any prompt/rule development set) for each executable `Application`.
- Measure and record precision, recall/opportunity coverage, false-alert rate, and runtime/indeterminate rate before approving an `Application` for pilot `preflight` routing.
- Restrict low-reliability or non-deterministic practices to `record-only` rather than noisy PR alerts.

## 5. Immutable Outcome Collection

- Implement authenticated, idempotent batched outcome writes (`.onemind/batches/<batch_id>/manifest.json`) verified against the trusted base commit.
- Capture full operational context: `eligible-pr`, `assignment`, `alert`, `action`, `repeat-correction`, `false-alert-adjudication`, `indeterminate`, `cost`, and `run-health`.
- Guarantee that checker errors, timeouts, and missing inputs always emit `indeterminate` events and are never counted as clean/passing runs.

## 6. Persistent Experimental Assignment

- Persist randomized treatment/control `assignment` events **before** any preflight check emits an alert or user-visible artifact.
- Bind assignment immutably to `(experiment_id, repo_id, pr_id)` so subsequent force-pushes or new commits on the same PR never switch arms.

## 7. Silent Control-Arm Isolation

- Run eligible checkers on control-arm PRs in silent mode only (for comparable opportunity accounting) while preventing any leakage of findings through PR comments, check-run annotations, job summaries, logs visible to PR authors/reviewers, or repo-wide `AGENTS.md`/`SKILL.md` edits.
- Add automated regression tests verifying zero control-arm visibility across all publication paths.

## 8. Statistical Preregistration and Outcome Measurement

- Publish a versioned preregistration document before prospective data collection begins, locking:
  - Unit of randomization (e.g., PR vs author/time cluster) and contamination mitigation.
  - Primary estimand (independently adjudicated unresolved violation rate vs repeated human review comments, without silent switching).
  - Minimum sample size / precision threshold, stopping rules, and handling of unreviewed/closed PRs (censoring).
- Ensure all outcome reports are pure, deterministic computations pinned to `(ledger_commit, application_versions, preregistration_version, analysis_code_version)` and honestly publish null or inconclusive results.

## 9. Privacy, Data Retention, and Reproducible Publication

- Define data classification, PII/secret redaction verification, retention windows, and retraction/right-to-delete workflows that reconcile compliance obligations with immutable ledger auditability.
- Decide public vs private dataset release boundaries before P2 collection begins, explicitly documenting in any published case study which inputs are redacted or non-reproducible.
