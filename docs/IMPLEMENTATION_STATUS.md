# OneMind Implementation Status

**Last updated:** 2026-11-22  
**Protocol version:** `0.1-draft` (`experimental`)  
**Current milestone:** M0 + M1 hardened local OSS substrate & CLI (M2 explicitly deferred)

---

## Completed Subsystems (M0 + M1)

| Area | Status | Implementation | Key Guarantees |
|---|---|---|---|
| **RFC 8785 Canonicalization (JCS)** | Implemented & tested | `src/onemind/protocol/canonical.py` | Full ES6 IEEE 754 binary64 numeric formatting (including subnormals & boundaries), UTF-16 code-unit key sorting, Unicode control escaping, surrogate rejection, safe-integer range enforcement, optional `reject_floats=True` fallback. |
| **JSON Schema & Cross-Record Validation** | Implemented & tested | `src/onemind/protocol/schema.py`, `validator.py`, `scope.py` | Draft 2020-12 structural schema + strict RFC 3339 timestamps; rejects unknown schema versions/kinds, duplicate IDs, dangling refs, practice/application version gaps, backdated decisions, bad approval digests, outcome provenance mismatches, assignment-after-intervention, and control-arm leakage; fails closed on unrecognized scope keys or unsupported glob syntax. |
| **Immutable Git-Backed Storage** | Implemented & tested | `src/onemind/storage/git_store.py`, `base.py`, `mutation.py` | Cross-thread (`RLock`) + cross-process (`fcntl.flock`) writer synchronization, atomic multi-record `append_many()` with rollback, atomic staged batch directory publishing (`append_batch()`), manifest SHA-256 integrity checks (`verify_batch_manifests()`), safe staging recovery (`recover_staging()`), and trusted Git base historical mutation checks (`verify_trusted_base()`). |
| **Governance & Deterministic Replay** | Implemented & tested | `src/onemind/governance/authority.py`, `policy.py`, `projection.py` | Separates `local-owner-assertion` from `verified-repository-approval`; rejects `agent` decision actors and forged authority upgrades; pure deterministic `EffectiveView(c, t, p)` failing closed on ambiguous applicability, simultaneous conflicting decisions, or unadjudicated contradictory practices. |
| **Transport-Independent Library API** | Implemented & tested | `src/onemind/api.py`, `engine.py`, `protocol/models.py` | Top-level `query`, `propose`, `propose_from_source`, `resolve`, and `observe` operating on deeply immutable objects (`ImmutableRecord`, `QueryCriteria`, `ResolutionContext`, `MappingProxyType`, `tuple`). Candidates and evidence remain strictly inert. |
| **Offline CLI (`onemind`)** | Implemented & tested | `src/onemind/cli.py`, `adapters/local_files.py`, `checks/` | `scan`, `inspect`, `propose`, `preview`, `validate` with `--json` and deterministic exit codes (`0`/`1`/`2`/`3`). Per-line source provenance, explicit `heuristic-scan` vs `explicit-capture` distinction, live disk drift check on inspect, built-in deterministic checker registry only (no arbitrary code execution). |
| **Offline End-to-End Demo** | Implemented & tested | `examples/api-compat-demo/`, `scripts/demo.sh` | Self-contained ADR + breaking/compatible JSON schemas + 7 canonical records with explicit `local-owner-assertion` labels; demonstrates genuine breaking failure, genuine compatible pass, explicit capture, and trusted-base validation without claiming enterprise enforcement or empirical improvement. |
| **Generic Workflow Composability** | Implemented & tested | `src/onemind/adapters/interfaces.py`, `markdown_retro.py`, `json_observation.py`, `examples/workflow-fixtures/` | Two unrelated synthetic formats (Markdown engineering retrospective & structured JSON observation) share `SourceAdapter` + `Extractor`, preserve independent provenance, ignore embedded authority claims, and remain independently removable. Zero external agent/workflow SDK dependencies verified by AST import audit. |

---

## Known Limitations (M0 + M1)

- **No live GitHub API / cryptographic merge proof verification:** `DefaultAuthorityVerifier` validates proof shape and policy membership locally; live verification against GitHub branch protection and commit reviews is deferred to M2.
- **Trusted base must be supplied externally:** `onemind validate` without `--trusted-base <commit-or-ref>` validates internal consistency of the current snapshot and explicitly reports historical mutation verification as `unavailable`.
- **Single-repository trust domain:** Multi-repo federation, enterprise IAM/SSO, HTTP/MCP transports, and hosted services are intentionally out of scope for M0 + M1.
- **No empirical effectiveness claims:** Demo fixtures and synthetic outcome events are illustrative only and do not measure real-world engineering improvement.

---

## Explicitly Deferred to M2

See [`docs/M2_BLOCKERS.md`](M2_BLOCKERS.md) for all nine prerequisites blocking M2 (live GitHub ingestion with coverage accounting, verified reviewer/protected-merge proofs, fork-safe Actions privilege separation, held-out detector evaluation, immutable outcome collection, persistent assignment, silent control-arm isolation, statistical preregistration, and privacy/retention policy).
