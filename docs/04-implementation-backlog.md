# OneMind v0.1 — Prioritized Implementation Backlog

**Status:** Proposed work breakdown; not a project schedule or statement of completed work.

## Guiding constraints

- One repository, one trusted governance domain, local-first, no hosted service.
- No inferred/imported policy activation, no auto-promotion.
- Preserve the original P0–P3 design gates and publish null results.
- Both developer utility and scientific inference must be demonstrated independently.

## Milestone M0 — Draft protocol, fixture and trust-model freeze

**Objective:** Establish a thin, credible data core before adapters or UI.

- [ ] `record.schema.json` + versioning and field definitions reviewed.
- [ ] Canonical SHA-256 implementation (RFC 8785) + cross-language test vectors.
- [ ] Cross-record reference and sequence validator, rejects duplicate IDs/changes/deletions against trusted base.
- [ ] Separate `GovernanceDecision` from `PracticeVersion`; no candidate-initiated state changes.
- [ ] Application must be scoped no wider than practice, separately authorized.
- [ ] `resolve(as_of, context)` pure replay with `active/ambiguous/indeterminate` outcomes.
- [ ] Security fixtures for malformed records, stale approvals, candidate conflicts, authority bypass and replay.
- [ ] Formalize data-use policy and redaction/retention modes.

**Done when:** two independent implementations/test paths agree on effective-state fixture corpus, mutation tests fail, and local owner claims cannot impersonate verified repo governance.

## Milestone M1 — Five-minute account-free demo

**Objective:** Demonstrate utility without Github, a network call or an AI provider.

- [ ] Publish Python packaging and `onemind scan`, `inspect`, `propose`, `preview`, `validate`, `--json`.
- [ ] Import local instruction and ADR sources as evidence/unverified candidates.
- [ ] Optional manual capture; no writes until user confirmation.
- [ ] Fully synthetic `api-compat-demo` including deterministically failing/passing code revisions.
- [ ] Explainability output for source, approval status, applicability, checker and result.
- [ ] Usability test with someone uninvolved in building OneMind.
- [ ] README, offline walkthrough, security/data-use policy, demo screenshots or terminal transcript.

**Done when:** external maintainer can discover a real existing convention, create an inert candidate, and preview a deterministic check; zero telemetry, account, model token or required daemon.

### Composability acceptance slice within M1

- [ ] `SKILL.md` / agent-instruction imports remain evidence and never acquire approval from their source file alone.
- [ ] Test a HumanMaxxing-based read-only fixture (user-selected retro/skill artifacts) and an unrelated framework fixture against the **same** import contract; no dependency on either runtime.
- [ ] Explicit `unsupported/indeterminate` on unknown input schema; immutable provenance, opt-in paths, and offline operation.
- [ ] Document workflow-reference profile in [`06-composable-workflows-humanmaxxing.md`](06-composable-workflows-humanmaxxing.md). No writeback, prompt injection, or automatic deployment of third-party hooks in M1.

## Milestone M2 — Governed GitHub pilot

**Objective:** Complete the original PR-review learning closed loop.

- [ ] GitHub SourceAdapter with pagination, version references and coverage accounting.
- [ ] Replaceable extractor: braindump-inspired pipeline, optional model, no implicit provider usage.
- [ ] Proposal PR containing source-linked candidates and draft versions.
- [ ] AuthorityVerifier for eligible reviewers, exact-content binding, protected merge and post-merge check.
- [ ] Conflict reports, candidate hold and authorized adjudication (no implicit disable).
- [ ] Trusted base-branch preflight evaluator and separate restricted publisher/ledger writer.
- [ ] App validation on held-out PRs before intervention.
- [ ] Assignment event persisted once per eligible PR, preserved across revisions, silent control arm.
- [ ] Batched ledger writes; explicit run-health/indeterminate events; idempotent retries.
- [ ] Pure report computation with pinned cutoff, clear estimand, contamination/censoring notes.
- [ ] Synthetic sandbox repo including malicious review fixtures and fork PRs.

**Done when:** an external maintainer can approve/merge and run the pilot without author intervention, the control arm sees no alerts/rules, and independently recomputed outcome reports match.

## Milestone M3 — Open beta and evidence-backed case study

- [ ] Choose public consenting pilot repo OR publishable redacted data that supports re-computation.
- [ ] Publish preregistered protocol and report version before observing prospective outcomes.
- [ ] Run blinded evaluation of extracted candidate accuracy vs baseline; separately evaluate checker reliability.
- [ ] Report opportunity counts, treatment assignment, corrections, confirmed fixes, audit disagreements, false alerts, missingness, costs, and uncertainty.
- [ ] Release Apache-2.0 repo with LICENSE, NOTICE if needed, CONTRIBUTING, DCO, SECURITY, CODE_OF_CONDUCT, RFC template, compatibility matrix.
- [ ] Invite maintainers to contribute one deterministic practice-pack fixture and one connector conformance test.

**Done when:** users can reproduce the dataset transformation and results as far as data-release constraints permit; report explicitly states what cannot be reproduced and makes no unearned quality or causal claims.

## Deferred after measured demand

MCP query adapter, independent GitLab source adapter, first curated practice pack, SQLite/Postgres store, JSON-LD/PROV-O export and round-trip, ADR/incident extractors, multi-repo sync, hosted OneMind Cloud, agent-runtime integration, auto-promotion and confidence decay, semantic conflict logic beyond narrow scope checks.

## First issue breakdown for coding agent

1. `feat(spec): record envelope/schema and fixtures` — validate kind payloads and strict required fields; no authority inferred.
2. `feat(ledger): immutable Git store and trusted-base diff validator` — reject edit/delete/duplicate/dangling refs.
3. `feat(governance): exact-digest approval record and pure effective-state projection` — separate local/verified modes, stale proof invalid.
4. `feat(cli): scan and inspect locally offline` — parse selected source types with source path/line provenance.
5. `feat(cli): propose and preview` — inert candidates only, deterministic local checker fixture.
6. `test(security): poisoned content, unauthorized approval, conflicting candidates` — establish invariants.
7. `docs: five-minute quickstart + demo fixture` — run without external credentials.
8. `feat(github): evidence ingestion with completeness and redaction policy` — later gate.
9. `feat(action): trusted preflight with verified policy, silent-control experiment` — later gate.
10. `feat(report): immutable outcome batch and replayable analysis` — later gate.

## Decision log to resolve before M2

- Exact approval proof schema and protected-branch checks (security-critical).
- Whether P2 primary is independently verified unresolved-violation rate, or human repeated comments as in the original design with audited violations as secondary; **pre-register and never silently switch**.
- Measured statistical power/eligible PR count and handling reviewer/author contamination.
- Repository ownership, employer OSS approval, name availability and hosting organization.
