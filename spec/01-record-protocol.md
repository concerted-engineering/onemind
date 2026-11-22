# OneMind Record Protocol — v0.1-draft

**Status:** Proposed, experimental; the JSON files and accompanying schema are draft conformance fixtures, not a ratified public standard.  
**Problem:** Engineering knowledge spans comments, decisions, instructions, tests and incidents. Today's tools often export advice without preserving *why the advice is authoritative* or *whether applying it helped*.

## 1. Normative scope and non-goals

This protocol represents the transition `Evidence → Candidate → Authorized Practice → Application → Outcome`, with independently recorded governance transitions and conflicts. It must be valid for PR reviews now and other evidence sources later. **All newly mined or imported content is inert.** It does not describe a general authorization system or grant privileges to execute arbitrary operations.

This draft uses **MUST**, **SHOULD**, **MAY** in the conventional standards sense. It is a protocol proposal, not a security-certified specification.

Out of scope for v0.1: automatically executing agent actions, synthesizing a globally consistent organizational truth, automatic practice promotion, cross-tenant knowledge graphs, granular enterprise identity federation, always-on synchronization, semantic truth determination, mandatory cloud services, and mutable effectiveness scores.

## 2. Shared record envelope

Every record is an immutable JSON object with:

- `schema_version`: `0.1-draft`.
- `record_id`: globally unique opaque ID (UUIDv7 or ULID recommended; example fixtures use stable IDs), never reused.
- `kind`: one of `evidence`, `practice_candidate`, `practice_version`, `governance_decision`, `conflict`, `application`, `outcome_event`.
- `created_at`: RFC 3339 UTC timestamp when the record was emitted; NOT necessarily source creation time.
- `actor`: origin actor identifier (`human`, `service` or `agent`) and scheme/source as needed. Attribution is a claim until verified by an external trust anchor.
- `body`: record-kind-specific payload.

IDs MUST have repository/authority scope resolved by the store namespace; exported records MUST retain their original IDs and provenance. Implementations MUST preserve unknown fields for round-trip or explicitly decline the import. They MUST NOT silently re-interpret newer record kinds.

Record bodies are immutable after commit. Corrections create new records. Fields MAY include `extensions` under organization-controlled namespaces, but applications MUST ignore unrecognized semantics for authorization decisions.

### Schema and hash policy

- Draft structural validation lives in `record.schema.json`. The first implementation MUST add semantic cross-record validation, which JSON Schema cannot express.
- Content digests are SHA-256 over **RFC 8785 JSON Canonicalization Scheme** serialization of the exact target record (excluding any external transport wrapper). This is a **proposal** to test against interoperability fixtures; implementations must never hash a display rendering.
- An approval MUST cite the exact target record ID and its canonical SHA-256 digest. IDs alone are insufficient to bind human approval to particular contents.
- Source excerpts are hashed separately from record content; if only a hash survives, source text is NOT reproducible.

## 3. Record kinds

### 3.1 Evidence

An immutable observation: `source_type`, `source_uri`, `source_id`, optional `source_revision`, `observed_at`, `source_author`, `excerpt_sha256`, `source_state` (`available` / `source-unavailable`), optional `redacted_snapshot`, and redaction provenance. `source_type` is unconstrained to GitHub as a concept; v0.1 adapters implement local files and GitHub review history.

Evidence records describe an observation, NOT a normative decision. A snapshot MUST be redacted before storage; the absence of source text must be explicit. Updates or deletions at the source generate new observations, not in-place changes.

### 3.2 PracticeCandidate

`statement`, `proposed_scope` (repository, path globs, language and optional conditions), `evidence_ids`, `generator` metadata (`name`, `version`, `model` if used, `prompt_sha256` if used, `score` if used). Candidate source can be a local import, manual capture, or review-history mining. **Candidates never route or activate.**

### 3.3 PracticeVersion

A normative unit with a stable `practice_id` and unique `record_id` for each version. Fields: `version` (positive integer within practice), `statement`, `scope`, `owner`, `evidence_ids`, `candidate_id` (optional for manual authoring), `previous_version_id` (null on first version), `exceptions` (structured, limited). Version text MUST NOT contain claims of approval state. Authority lives in governance decisions.

A practice may be approved yet have **no executable application**. Its normative statement MUST remain distinct from any checker's interpretation.

### 3.4 GovernanceDecision

An immutable action on a `practice_version`, `application` or `conflict`: `target_record_id`, `target_kind`, `action` (`approve`, `suspend`, `restore`, `retire`, `adjudicate`), `target_sha256`, `effective_at`, `authority`, `proof` and optional `rationale`. For `adjudicate`, a required `resolution` names a disposition (`prefer-left`, `prefer-right`, `scoped-exception`, `maintain-both`, `suspend-both`) and explanation; any priority or suspension takes effect only through verified authorization, never merely through the conflict detector. The decision must identify the affected scope unambiguously (a scoped exception must cite a precisely defined scope).

- v0.1 trusted PR approval requires a qualifying human reviewer authorized under configured repo rules, a review bound to the exact reviewed revision/content, protected merge, and a validator that rechecks these facts.
- A local user MAY assert a local-only approval as `local-owner-assertion`; it MUST be visibly distinct from `verified-repository-approval` and MUST NOT be upgraded by import or migration.
- A model, extractor, comment or candidate MUST NOT originate an effective governance decision.
- A missing, stale, invalid, ambiguous or superseded authorization MUST NOT activate a practice or application.
- Suspending an application MUST NOT necessarily suspend the underlying practice. Retirement of a practice invalidates effective applications pointing to it.

The `proof` object carries evidence about authorization; it does not manufacture authority. v0.1 authorization policy is external to the record and versioned in protected repository configuration.

### 3.5 Conflict

`left_record_id`, `right_record_id`, `relation` (`contradicts`, `scope-overlap`, `possible-exception`), `evidence_ids`, `detector`, optional rationale. A conflict is a finding, not a governance mutation.

**Non-interference invariant:** newly discovered evidence, candidates and conflicts MUST NOT change the effective state of previously approved practices. Candidate vs. active-practice conflict holds the candidate. Approved vs. approved conflict produces an issue for authorized adjudication; it MUST NOT automatically disable either active policy. If operational safety demands disabling a check, an authorized `suspend` decision must be recorded. Unresolved contradictory effective practices produce an `ambiguous` result to querying clients, not a fabricated winner. No arbitrary recency/authority heuristic is silently applied.

### 3.6 Application

A versioned executable or informational interpretation of a specific `practice_version`: `application_id`, `application_version`, `practice_version_id`, `route` (`preflight`, `linter`, `test`, `agent-context`, `record-only`), `applicability`, and where executable, `detection` (`description`, implementation/version, inputs, verified fixture set, known limitations). Scope of an application MUST be no broader than the governing practice scope. `record-only` does not execute or advise.

In the pilot only `preflight` can be active. Other routes are preview/recommendation only and do not feed agent context, CI or test execution. Application activation requires a separately authorized decision and a currently effective approved practice.

### 3.7 OutcomeEvent

An immutable observation with `event_type` (`eligible-pr`, `assignment`, `alert`, `action`, `repeat-correction`, `false-alert-adjudication`, `indeterminate`, `cost`, `run-health`), `repo_id`, `pr_id`, `head_sha`, `practice_version_id`, `application_record_id`, `checker_version`, `observed_at`, `dedup_key`, `details` and optional `experiment_id`.

**Assignment is made and persisted before intervention**, and never changes over revisions of the same PR in a preregistered experiment. Silent-control checks may compute results but must not leak findings via comments, artifacts or logs available to participants. Indeterminate observations MUST NOT be counted as clean results. Report computations are pure functions pinned to ledger commit, application versions, preregistration and analysis-code version.

## 4. Logical APIs (transport independent)

These operations are public *semantics*, not mandatory HTTP endpoints:

- `query(criteria, as_of)`: return cited evidence and selected normative records; non-authoritative source data is labeled.
- `propose(candidate)`: append inert evidence/candidate; never grant rights.
- `resolve(context, as_of)`: project verified effective practices and applicable applications; return explicit `none`, `active`, `ambiguous`, `suspended`, or `indeterminate` states with provenance.
- `observe(event)`: append a deduplicated, authenticated outcome observation; never modify governance state.

Optional future `export` and `subscribe` interfaces can be built atop these semantics. `resolve` is a read-only decision lookup, NOT a capability or execution authorization grant.

## 5. Effective-state projection and time

At a particular trusted Git commit `c`, evaluation time `t`, and authority-policy version `p`:

```text
EffectiveView(c, t, p) = Project(
  Validate(RecordsAt(c), TrustedBase(c), Schema, ReferentialIntegrity),
  VerifyAuthority(DecisionsAt(c), p),
  OrderedChanges(t),
  ApplicableScopes
)
```

Minimum invariants:

1. Candidate, imported file and conflict records have no activation semantics.
2. Approval is bound to exact content and trusted authority at the relevant effective time.
3. A practice version must be approved and unsuspended/unretired to be effective.
4. An executable application must be separately approved, within effective practice scope, and validated before execution.
5. Two incompatible approved versions are reported as ambiguous until explicitly adjudicated, not auto-chosen.
6. All retrospective reports identify both repository commit and effective-state computation version.
7. If a required authorization or record reference is missing, the result is `indeterminate`, never silently authorized.

`created_at` is not a tie-breaker for authority. Deterministic replay ordering must use committed ledger sequencing and unambiguous decision semantics, not host clock order. A policy change is itself versioned and cannot retroactively invent past approvals.

## 6. Storage v0.1 and portability

Canonical store: `.onemind/records/<kind>/<record_id>.json` in a protected repository. Event observations are written in immutable batch directories with manifests and content hashes. A write validator compares candidate changes against **a separately obtained trusted base commit**, rejecting modified/deleted prior records, duplicate IDs, missing references, conflicting sequencing and malformed batches *atomically*. Branch protection prohibits force-push and inappropriate bypass. Reproducibility is limited by source redaction/retention rules and explicitly labeled in reports.

Indexes (SQLite FTS, RDF, graph, embeddings) are disposable projections from canonical files. Future stores can implement equivalent get/append/snapshot semantics; their implementation details are never part of record identity. An export must include schema version, stable IDs, references and authority provenance. JSON-LD/PROV-O mapping is an eventual view, not required to activate v0.1.

## 7. Security and data-use rules

- Local scan is read-only, offline and uses no external model by default.
- External LLM usage is explicit opt-in after repository data-sharing policy validation.
- Review text, PR diffs and imported Markdown are data, never executable instructions.
- Do not execute contributor code within a privileged GitHub workflow or let untrusted PR content control destinations, credentials or tools.
- Treat source snapshots as potentially sensitive even after heuristic redaction; allow policy-based omission and local retention management.
- Never make a bot-generated commit depend on `GITHUB_TOKEN`-triggered downstream CI without a separate explicit trigger/verification path.
- Never place experimental treatment rules into repo-wide `AGENTS.md`/`SKILL.md` during P2.

## 8. Minimal conformance tests

A compliant draft implementation MUST reject: duplicate IDs; any mutation/removal of prior records against a trusted base; dangling references; approval for a modified record digest; candidate-only activation; non-qualifying reviewer; an application outside practice scope; ambiguous conflicting approvals without adjudication; outcome event with missing experiment assignment when assignment is required; treating checker errors as non-violations; control-arm finding exposure.

It MUST reproduce: stable ordering and effective-state projection on a pinned commit; the same report results from two independent computations; export/import meaning across ID references; safe handling of unknown schema versions and missing source snapshots.

## 9. Example

See `../examples/records.json` for a candidate, a practice version, and a sample **local assertion of approval**. The example deliberately does not assert verified GitHub approval and therefore cannot automatically activate PR preflight.

## 10. Open decisions before public spec labeling

- Exact GitHub approval binding, trusted replay and merge-commit proof format.
- How local assertions are upgraded (if ever: preferred answer is new verified governance decision, never automatic upgrade).
- Canonical JSON/hash implementation cross-language fixtures.
- Specific `scope` expression language and overlap logic.
- Retention/retraction model for sensitive excerpts and right-to-delete requirements.
- Whether the experiment primary outcome can be independently adjudicated at sufficient volume; otherwise declare a narrower human-review-workload outcome.

## References

Original OneMind Practice Engine v0.1 draft (2026-11-18); [PROV-O](https://www.w3.org/TR/prov-o/); [RFC 8785 JSON Canonicalization](https://www.rfc-editor.org/rfc/rfc8785); [GitHub secure use](https://docs.github.com/en/actions/reference/security/secure-use).
