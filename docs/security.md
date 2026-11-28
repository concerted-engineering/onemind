# OneMind Security Model & Threat Boundaries (M0 + M1)

**Protocol status:** `0.1-draft` (experimental). This document describes the threat model, enforced invariants, and explicit limitations of the local M0 + M1 implementation.

## 1. Core Security Invariants

1. **Untrusted content is data, never instructions or policy.**
   - Repository Markdown (`AGENTS.md`, `SKILL.md`, ADRs, retrospectives) and JSON observations are parsed strictly as inert data.
   - Importing documentation or workflow fixtures never creates a `practice_version` or `governance_decision` and never activates checks.
   - Heuristic display extraction strips ANSI/control characters and ignores fenced code blocks.

2. **No arbitrary code execution from records.**
   - `onemind preview` never executes scripts, shell commands, or Python files named in imported `application` records.
   - Only audited built-in deterministic checkers registered in `src/onemind/checks/registry.py` (`builtin:api-compat-json`) can run. Unknown implementation identifiers return `indeterminate` (exit code `3`).

3. **Strict governance and authority separation.**
   - `actor.type == "agent"` can never originate a valid `governance_decision`.
   - Every governance decision must bind to the exact `target_record_id` and its RFC 8785 canonical JSON SHA-256 digest (`target_sha256`).
   - `local-owner-assertion` (`verified_repository_approval: false`) is visibly separated from `verified-repository-approval` and cannot activate practices under default strict repository policy (`allow_local_assertions=False`). Attempts to set `verified_repository_approval: true` on a `local-owner-assertion` are rejected (`FORGED_AUTHORITY_UPGRADE`).
   - Unknown authority modes, stale/revoked proofs, unauthorized reviewers, and unrecognized `extensions` fields fail safely (non-activation).

4. **Non-interference and scope containment.**
   - Newly discovered evidence, candidates, and candidate-vs-practice conflicts never disable previously approved practices.
   - Unresolved `contradicts` conflicts between two approved practices return `ambiguous`, never a silently chosen winner.
   - An `application`'s `applicability` must be statically provable to be a subset of its governing `practice_version.scope`; ambiguous or wider scopes are rejected (`APPLICATION_SCOPE_OVERREACH`).

5. **Immutability against a separately established trusted base.**
   - `GitRecordStore.append()` writes with `O_CREAT | O_EXCL` and refuses duplicate `record_id` values.
   - `onemind validate --trusted-base <commit-or-ref>` verifies every record and batch file present at `<trusted-base>` against the current working tree, detecting modifications, deletions, and symlink replacements.
   - Without `--trusted-base`, historical immutability verification is explicitly reported as `unavailable`.

6. **Filesystem and offline safety.**
   - `record_id` and `batch_id` values are restricted to safe characters without path separators or `..`.
   - `scan`, `inspect`, `propose`, and `preview` block path traversal (`..`) and symlink escapes outside the repository root.
   - `scan` enforces a per-file byte limit (`1 MiB` default) and gracefully skips binary or non-UTF-8 files.
   - All M0 + M1 commands (`scan`, `inspect`, `propose`, `preview`, `validate`) are 100% offline with zero network calls, zero external LLM calls, and zero telemetry. Read-only commands never modify the working tree.

7. **M2A read-only GitHub ingestion security & privacy (`onemind ingest github`).**
   - Opt-in network access only when explicitly invoked without `--fixture-file`; zero GitHub writes, zero LLM calls, zero telemetry.
   - HTTP transport enforces a single configured HTTPS origin (default `https://api.github.com`), blocks cross-origin/scheme-downgrade redirects and malicious `Link` headers, detects pagination loops, validates JSON `Content-Type`, and caps response payload size (`5 MiB` default).
   - Default `DataCapturePolicy` is `metadata-only` (raw review text never stored on disk). Opt-in `redacted-excerpt` applies deterministic secret/PII redaction and documents its heuristic limitations.
   - Tokens are read only via `--token-env NAME` (never CLI args), never stored in records/manifests, and scrubbed from all diagnostics and exception messages.
   - Console and `--json` outputs emit safe counts, IDs, and coverage only—never raw review bodies or tokens.
   - Persisted `IngestionRun` manifests under `.onemind/ingestions/` are immutable and verified by `onemind validate --trusted-base`.
   - Ingested GitHub reviews and comments are strictly inert `Evidence`; GitHub review states (`APPROVED`, `CHANGES_REQUESTED`) never confer OneMind governance authority.

## 2. Known Security Limitations in M0 + M1

- **No external cryptographic or GitHub API proof verification yet.** `DefaultAuthorityVerifier` validates proof structure and policy membership locally, but live verification against GitHub branch protection, signed commits, and merge-commit reviewer APIs is deferred to M2 (see `docs/M2_BLOCKERS.md`).
- **Local repository tampering without a trusted base.** If a user runs `onemind validate` without supplying an independently trusted Git commit/ref (`--trusted-base`), OneMind can validate internal schema and digest consistency of the current snapshot, but cannot detect rewritten history or deleted records.
- **Best-effort redaction responsibility.** Local scanning and manual `propose` capture excerpts directly from local files; users must avoid committing secrets or sensitive data into `.onemind/records/`.
- **Single-repository trust domain.** Cross-repository authority federation, RBAC, and multi-tenant identity verification are out of scope for `0.1-draft`.
