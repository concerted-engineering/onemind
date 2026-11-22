# M2 Blockers & Prerequisites (Explicitly Deferred from M0 + M1)

**Status:** M2 (Governed GitHub Pilot) is **not implemented** in M0 + M1. No empirical claims about engineering improvement may be made from the synthetic M1 demo or workflow fixtures.

Before enabling live GitHub PR review mining, active preflight, or prospective experimentation in M2, every prerequisite below must be designed, security-reviewed, and backed by automated conformance tests.

## 1. Real GitHub Evidence Ingestion and Coverage Accounting

- Implement a read-only GitHub `SourceAdapter` supporting cursor pagination, rate-limit backoff, and explicit retry limits.
- Record complete coverage accounting per ingestion run (`run-health` / `indeterminate` events) whenever pagination is truncated, API errors occur, or comments/reviews are deleted or inaccessible (`source_state: "source-unavailable"`).
- Support explicit repository data-sharing opt-in and configurable excerpt redaction/omission policies before writing any evidence to Git.

## 2. Verified Reviewer Authority and Protected-Merge Checks

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
