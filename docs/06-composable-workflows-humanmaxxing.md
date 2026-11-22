# RFC-0002: Composable Engineering Workflows — HumanMaxxing Reference Profile

**Status:** Proposed, non-normative integration profile for OneMind v0.1.  
**Date:** 2026-11-20  
**Scope:** One implementation angle of Concerted Engineering: AI-assisted software execution, review, shipping, and retrospectives.  
**Reference:** [HumanMaxxing public OSS repository](https://github.com/pgartlan/humanmaxxing), inspected November 20, 2026. This profile is an **independent proposal**; it does not imply any claim, endorsement, obligation, or roadmap commitment by HumanMaxxing's maintainers.

## Decision

OneMind **MUST** be composable with independently operated engineering workflows, rather than specifying one blessed workflow or rewriting other products' orchestration. HumanMaxxing is the first concrete **reference integration profile**, not a OneMind dependency, exclusive partner, or substitute for Concerted Engineering.

**Invariant:** Each external workflow remains useful without OneMind, and OneMind remains useful without that workflow. An integration MUST be independently installable/removable and MUST NOT become an authority-verification shortcut.

## OneMind's responsibility versus the workflow's responsibility

- **Workflow frameworks** own scheduling, agent selection, planning, task execution, human interactions, review gates, and their own tool-specific configuration.
- **OneMind** owns portable representations of source evidence, proposals, authorized decisions, scoped practices, application versions, and verified/qualified outcome observations. OneMind can resolve historical effective state without requiring the originating workflow.
- **Adapters** translate between artifacts/events and these contracts. Adapters do **not** infer approval from a retrospective, an instruction file, a successful hook, or an agent's asserted judgment.
- **Concerted Engineering** is the overarching operating model, not a mandatory runtime dependency.

## Reference profile grounded in the HumanMaxxing public README

The public repository describes a skill-based framework with `start`, `review-plan`, `review-code`, `peer-review`, `checkpoint`, `ship`, `retro`, and other skills; `.claude/` templates; agent hooks; and installation tooling. It describes `/retro` as creating a retrospective report of recurring review findings and proposing changes subject to user approval. Its README mentions review sentinels produced by `review-code` and checked by `ship`. **The public README does not establish a stable machine-readable event API or precise retro output schema.** A first adapter should use explicit user-supplied paths and validated, version-pinned parsers, not conjectured locations or a purported official integration.

### Composition mapping

| Workflow artifact/action | OneMind relationship | Initial support | Boundary |
|---|---|---|---|
| Repository-local `SKILL.md`, `CLAUDE.md`, templates | `SourceAdapter` creates cited `Evidence`; optional inert `PracticeCandidate` | M1 read-only | Imported instructions are not approved policies |
| User-selected `/retro` report | `SourceAdapter` ingests report excerpts and source links; `Extractor` proposes candidate lessons | M1 fixture; real parser after artifact validation | A suggested rule is not an authorization |
| `/review-plan`, `/review-code`, `/peer-review` | `query` / `resolve` supplies practices and linked evidence as *preview or advisory* | M2 integration preview | No modification of third-party skills or prompts by default |
| `/ship` / review sentinel | Adapter may translate independently verifiable PR/commit/checker facts to a scoped observation | Post-M2, only for events covered by protocol | Never infer code quality or causal improvement from a successful ship |
| Hooks and tool-specific gates | `ApplicationAdapter` can provide an approved check for explicit opt-in consumption | Future | Execution belongs to the workflow; policy authority stays with OneMind/governance owners |

## Minimal composition contract

OneMind's existing transport-neutral operations remain the protocol surface:

- `query(criteria, as_of)` — evidence and source-linked practice information.
- `propose(candidate)` — inert new proposal; any source may propose, none may self-approve.
- `resolve(context, as_of)` — effective practices, versions, proofs, and explicit ambiguous/indeterminate outcomes; this is **not** execution permission.
- `observe(event)` — authenticated, append-only observations for supported event types only.

A generic `WorkflowAdapter` is a **convenience bundle** of capabilities, not a fifth mandatory protocol API. An adapter manifest should declare capabilities and constraints, for example:

```yaml
id: example.humanmaxxing
adapter_api: onemind.workflow-adapter/v0alpha
source: https://github.com/pgartlan/humanmaxxing
source_revision: <configured-and-pinned-revision>
capabilities:
  - evidence.import.local-skill-files
  - evidence.import.user-selected-retro-report
  - practice.resolve.preview
# observation.import intentionally absent until verifiable event mapping exists
permissions:
  repository_read: true
  repository_write: false
  network: false
trust:
  imported_policy_authority: none
  automatic_approval: false
```

The example manifest is proposed OneMind configuration, **not** a file currently supplied by HumanMaxxing. Integrations should declare source versions, parsers, required permissions, data destinations, supported schemas, and known gaps.

## Illustrative end-to-end composition

1. Team uses HumanMaxxing `/retro` to produce a report about recurring review corrections.
2. A user explicitly imports that report: `onemind import --adapter humanmaxxing --file <reviewed-report-path>` (proposed command; not implemented).
3. OneMind stores source-linked evidence and suggests an **inert candidate** about backward compatibility.
4. An authorized maintainer reviews and approves a precise practice version under the configured OneMind authority policy.
5. A separately approved deterministic preflight application checks the practice in a future PR. A human or agent workflow may **query/preview** this practice, but the randomized prevention pilot must not expose control-arm findings or repo-wide instructions.
6. The preflight produces verifiable observations; the outcome report links opportunities, intervention assignments, code revisions, and adjudication status. Any claims of reduced defects or review effort require suitable measurement, not just a success flag from `/ship`.
7. The same practice can be consumed by a different workflow adapter without changing the authoritative record.

This uses HumanMaxxing as an independent source/consumer on **one angle** (AI coding workflow) and OneMind as the shared substrate. Nothing requires editing HumanMaxxing upstream.

## Trust, security, and non-interference rules

- Default to **read-only, offline, opt-in import**; no repo changes or automatic publishing.
- Untrusted markdown/skill/retrospective content is parsed as **data**, not executed, not injected into privileged prompts, and not treated as authoritative.
- Approval requires separate OneMind governance validation; a workflow's own approval and OneMind practice approval have distinct meanings.
- Resolve results must include scope, version, provenance, limitations, and `active/ambiguous/indeterminate` state. Clients cannot treat `indeterminate` as approval.
- A workflow's claimed successful run is a **self-reported observation**, not ground truth. Outcome reporters must distinguish source assertions from independently verified code/check results.
- Do not alter vendor-specific skill files or hooks automatically. Explicit user approval is required for output materialization.
- Prevent leaking private reviews/retrospective text to hosted providers or public telemetry by default.
- An adapter must not disable existing approved practices, bypass external execution controls, or alter experiment assignment/exposure.

## V0.1 versus future scope

**M1 (implement first):** Generic source import of `SKILL.md` and user-selected local reports into immutable evidence; representative HumanMaxxing static fixture; no special dependency or external execution. Tests verify stable provenance and non-activation.

**M2 (pilot):** Add source-linked query/preview output that an external workflow can read, keeping the randomized PR study isolated. Do not change live HumanMaxxing prompts, skills, or hooks in the experimental repository.

**After pilot:** An opt-in `ApplicationAdapter` can deliver an approved, bounded practice view to an external workflow; a versioned observation profile can capture workflow events with explicit evidence quality. Add other profiles (e.g. independent review-agent and CI frameworks) to prove interface generality.

## Acceptance tests for composability

1. A HumanMaxxing fixture and an unrelated agent/workflow fixture both import through the same SourceAdapter contract into **the same core record schema**.
2. Importing any unverified skill or retro report never changes active/approved status.
3. After governance approval, `resolve` yields the same effective practice whether its evidence came from HumanMaxxing or a different source.
4. Removing the HumanMaxxing adapter leaves stored evidence, governed decisions, and prior outcome reports reproducible; any unavailable source is marked unavailable, not quietly removed.
5. A workflow can inspect a practice through a read-only projection, with no automatic file edits or hidden data-sharing.
6. Missing or mismatched upstream format produces an explicit `unsupported/indeterminate` result, never fabricated facts.
7. OneMind can pass these tests without network access, OneMind Cloud, or a HumanMaxxing runtime installation.
8. Experiment control-arm findings cannot leak through an agent-context adapter or generated instructions.

## Open design questions

- Is a richer neutral `WorkflowObservation` record warranted, or should reusable event metadata remain an extension of existing `OutcomeEvent`? **Do not force unsupported external activities into the v0.1 PR event enum.**
- How should adapters declare evidence assurance: source assertion, tool attestation, independent verifier, or adjudicated ground truth?
- When multiple workflow engines provide conflicting instructions, how should scope/authority be surfaced in an advisory view without allowing adapter-local precedence to become organizational policy?
- What narrow, versioned format should be exported to external agents without excessive context or instruction-injection risk?

## Product implication

The OneMind adoption promise is not 'use our workflow.' It is **'keep your workflow, retain the team's authoritative practices and learning across workflows.'** HumanMaxxing is a valuable reference case precisely because it exercises planning, review, shipping and retrospection without requiring OneMind to own those activities.
