# Coding agent handoff — Bootstrap OneMind M0 + M1

Use the following prompt in a coding agent **after** reviewing `README.md`, `spec/01-record-protocol.md`, `docs/02-developer-experience.md`, and `docs/04-implementation-backlog.md`.

---

Implement the first two milestones of OneMind v0.1 in a new Apache-2.0 Python repository. This is an OSS **local-first engineering practice substrate**, not a generic AI memory bot. The reference specification and JSON Schema in this package are drafts; reconcile any contradictions through an explicit decision log rather than silently inventing authority semantics. Keep the scope narrow and working end-to-end.

## Requirements

1. Create `engine/`, `spec/`, `examples/`, `tests/`, `docs/`, `rfc/`, and standard OSS project files. The Python package exposes an `onemind` CLI.
2. Implement immutable record models for `Evidence`, `PracticeCandidate`, `PracticeVersion`, `GovernanceDecision`, `Conflict`, `Application`, and `OutcomeEvent`. Validate both JSON Schema and cross-record invariants.
3. Implement a Git-native file store and a validator that compares proposed changes against a **trusted base commit**; reject all edits and deletions of existing records, duplicate IDs, invalid digests, dangling references, and malformed batches.
4. Use an explicit versioned authorization model: **local assertion is not a verified repository approval**. M0/M1 does not have to implement GitHub approval verification, but MUST NOT label any local approval as repository-verified or enable pilot PR checks.
5. Implement pure, deterministic projection of effective practices and application eligibility; stale or missing authority produces `indeterminate`/inactive; conflicting candidates cannot deactivate approved practices. Application checkers are separately versioned and require approval.
6. Add `onemind scan PATH` (offline/read-only), `inspect`, `propose` (inert only), `preview` (dry-run), `validate`, and `--json`. No model key, cloud account, external network, daemon, or implicit file writes. Record original source paths/line references and explicit source/heuristic labels.
7. Add `examples/api-compat-demo/` containing a sample ADR, intentional public API schema incompatibility, deterministic baseline-vs-change schema checker and a passing corrected change. The demo should work with no LLM.
8. Write tests: successful example schema validation, missing reference, invalid digests, mutated prior record, unauthorized activation, conflict non-interference, malformed input, unknown schema version, no network calls from scan and no source edits from scan. Print a concise failing test summary in CI.
9. Create concise README getting-started commands and an architecture/security page. Add a decision log entry for each clarification that materially changes the spec.
10. Do not implement Slack, cross-repo sync, MCP serving, hosted features, full graph/RDF, auto-promotion, GitHub Action, PR experiment, or autonomous agent execution during M0/M1.

## Definition of done

- A fresh developer can complete a no-account, no-model local walkthrough from the sample repo and see a source-linked finding and deterministic preview.
- All examples validate structurally and semantically.
- The only active authority class at this stage is explicitly labeled local owner assertion; no claim of verified organizational approval.
- CLI behavior is deterministic and safe for CI (read-only unless `propose` is explicitly confirmed).
- Explanations show provenance and uncertainty, not hallucinated policies.
- Include a `M2_BLOCKERS.md` enumerating the approval verification, trust isolation, event writer, and experiment preregistration work required before any live PR pilot.

Work in small reviewable commits, avoid generated code bloat, and do not claim performance or effectiveness improvements without measured evidence.
