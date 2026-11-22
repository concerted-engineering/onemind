# OneMind Developer Experience — v0.1 Proposal

**Status:** Implementation plan, not shipped features or published CLI commands.  
**Primary persona:** solo contributor or maintainer with an existing Git repository.  
**Secondary persona:** maintainer running a governed PR-review prevention pilot.

## 1. Design principle

**Five minutes to an honest useful result; no account, server, network, or API key.** A local developer should understand existing documented conventions, their sources, and how to create a reviewable practice without replacing the current coding agent.

Do not sell a blank local repo a magical autonomous learning system. The first scan finds explicit source documents, their provenance, and simple structural anomalies; **semantic conflict and practice mining require either user input or optional inference**. Early reports distinguish document discovery, inferred candidates, and verified approved practices.

## 2. Initial product slices

**DX-A: Account-free local scan (GA candidate).** Read `AGENTS.md`, recognized agent-instruction files, repository ADRs and documentation locations, plus optional user-specified paths. Show source inventory, candidate conventions, duplicate instructions and missing attribution **only when objectively detectable**. No network, LLM, or file writes. Works even when no historic PRs exist.

**DX-B: Explicit practice capture (GA candidate).** A developer can propose a statement with scope, evidence path/line, and owner. `propose` writes a diff-previewed candidate only after confirmation. Local owner assertions are shown as `locally-approved` and are not equivalent to verified organizational approval. A deterministic checker can be previewed locally on selected changes.

**DX-C: GitHub review-history mining (beta).** Opt-in, read-only GitHub token via `gh`, explicit data-sharing policy and optional model configuration. Extractor adapter creates inert source-linked candidates, using braindump ideas without treating its heuristics as universal. Generator provenance is recorded. No auto-publishing of rules.

**DX-D: Pilot PR preflight (research beta).** Verified GitHub approval+merge for practice and application, validated checker, trusted base-branch workflow, advisory findings, silent-control experiment and outcome ledger. Repo-wide AGENTS.md/SKILL.md files remain unmerged previews to prevent experimental contamination.

## 3. Proposed CLI (illustrative, commands not yet implemented)

```console
$ uvx onemind scan .
OneMind scan (offline, read-only)
Sources: 2 instruction files, 3 ADRs
Found: 7 source-linked convention statements (unverified)
Warnings: 1 duplicate source; 1 statement without a cited owner
No practices activated. No files changed. No data uploaded.

$ uvx onemind inspect --source docs/adr/0003-api-compat.md
# Original source, line anchors, hashes and candidate statements

$ uvx onemind propose --statement "Public API removals require compatibility review" \
    --scope "api/**" --evidence "docs/adr/0003-api-compat.md"
# Shows an inert PracticeCandidate diff; asks before writing files

$ uvx onemind preview --candidate <candidate-id> --route preflight
# Shows a proposed checker and applicability, never activates it

$ uvx onemind validate .
# Schema and cross-record diagnostics; no side effects
```

Additional beta commands (not in five-minute flow):

```console
$ onemind ingest github --repo OWNER/REPO --since 2026-01-01
$ onemind extract --source github --provider explicit-opt-in
$ onemind reconcile --dry-run
$ onemind propose-pr --repo OWNER/REPO
$ onemind check-pr --pr 123 --mode advisory
$ onemind report --at <trusted-commit> --experiment <id>
$ onemind doctor
```

These commands are proposed UX contracts, not compatibility commitments. Use `--json` output for automation; write human text to stderr if JSON is requested. Favor actionable errors and deterministic exit statuses (`0` success/no findings, `1` validated finding, `2` invalid input/config, `3` incomplete external data/checker failure) with output format pinned at release time. Secret values are never printed.

## 4. Installation and no-cloud default

- Python engine and CLI in the repository, packaged with a modern build backend; try `uvx onemind` **after publication on a package index**, not before. Offer `pipx` or `pip` as alternatives.
- No background daemon or account creation in DX-A/B.
- Local `.onemind/` records are ordinary reviewable files; small read-only indexes may be in a `.gitignore`-ed cache.
- Network access and external LLM inference are separately opt-in; GitHub access uses existing `gh` authentication or a documented token, not a new service login.
- No telemetry by default. Optional diagnostics must explain precisely what would be sent.

## 5. First repo demo (must be committed and reproducible)

`examples/api-compat-demo/` includes:

1. An ADR stating API backward compatibility requirements.
2. A deliberate candidate code change that removes a public response field.
3. A deterministic schema-diff checker with a fixture that succeeds and one that fails.
4. A manually created candidate with a source link and scope.
5. A local-only approval example and a separately annotated GitHub verified-approval fixture.
6. A recorded `alert`, a fixed-code revision, and an outcome report explicitly labeled **synthetic demo, not an experimental effectiveness result**.

The onboarding demo MUST make the trust distinction visible: finding an ADR or `AGENTS.md` does not automatically authorize new active checks.

## 6. Pilot workflow and guardrails

**Read phase:** a GitHub adapter retrieves review comments and PR revisions as data, snapshots redacted excerpts or explicit unavailable markers. Partial pagination/errors appear as run-health coverage gaps.

**Propose phase:** dedupe and score candidates, link each to evidence, classify conflicts for review. A maintainer sees a human-readable proposal PR and edits statements/scope before approving. A candidate cannot be enabled simply because an extractor rated it highly.

**Approval phase:** a trusted reconciler verifies protected-branch review requirements and binds the authorized reviewer+reviewed content digest+commit to the governance decision. The designated maintainer merges; validator rechecks the resulting records against a trusted base commit.

**Preflight phase:** trusted code runs from base; untrusted PR diff is read as data. Approved applications with retrospective validation run on both experimental arms; only treatment gets PR-visible findings. Results are advisory in pilot. The reporter never executes PR-contributed code under write credentials or secrets.

**Observe phase:** assignments are persisted before any alerts; results include eligible PR, checker, head commit, practice/application versions, observations, reviewer comments, pre-review diffs, costs and error states. Outcomes collected at review completion or closure, with explicit handling of unreviewed PRs. Bot writes use immutable event batches with independent validation; do not rely on ordinary `GITHUB_TOKEN` pushes to trigger workflow chains.

**Report phase:** versioned pure computation from a pinned commit and preregistration. Do not infer code-quality gains from fewer human review comments; sample independently adjudicated unresolved violations and report contamination/censoring. If volume is insufficient, mark prevention effect inconclusive.

## 7. Acceptance criteria, not marketing claims

### Local v0.1 acceptance

- Fresh machine runs the demo without model key, cloud account, or network (given installed dependencies).
- `scan` completes in an onboarding session and **never writes** to source or sends data.
- Source inventory is correct against fixed fixtures, with line/source traceability.
- Candidate remains inert after import, creation and even merge without verified approval.
- Conflicting candidate never suppresses an approved application.
- Every user-visible inference has a provenance label (source / heuristic / human assertion / authorized).
- Generated record passes JSON Schema and cross-record validation.
- Approved practice and checker versions can be explained independently.
- Unknown schema version causes a clear error, not a silent best-effort authorization.

### Experimental beta acceptance

- Actions path handles forks without privileged untrusted execution.
- All active checks are tied to qualifying governance decisions and exact digests.
- Same PR maintains assignment across revisions; control never sees treatment rules/findings.
- Outcome events cover incomplete sources/checker errors and can be replayed deterministically.
- Retrospective detector test uses held-out PRs.
- Pre-registration includes unit of randomization, primary outcome, reviewer timing, sample/precision threshold, censoring, cross-arm exposure, and reporting cutoff.
- Public reproducibility is decided before P2 dataset collection.
- The pilot publishes null/inconclusive results honestly.

## 8. Accessibility and community UX

- Keep user-facing terminology simple: **source**, **suggestion**, **approved practice**, **check**, **result**. Expose the ontology to integrators, not on every CLI screen.
- Write a concise `README` quickstart, one narrated terminal demo, an example PR, and a security model page.
- Provide `--explain` on every check: exact practice text, source, authority, applicable scope, checker version, and what result means.
- Prefer actionable diagnostics to opaque generated-rule confidence percentages.
- Include a `no-ai` tutorial and a documented opt-in provider path.

## 9. Explicit exclusions

No Slack connector, no hosted UI, no organizational roll-up, no MCP server in the first local release, no prompt-injection-driven tool execution, no automatic editing of AGENTS.md, no treating a mined comment as organizational policy, no autonomous change of governance decisions.

## 10. Rollout and kill criteria

- **Usability:** a maintainer not involved in development completes the demo and explains one practice's provenance without author help. If not, revise the experience before adding connectors.
- **Extraction:** blinded review shows material, source-accurate candidates in a sufficiently rich repository. If not, focus on explicit capture and import rather than large-scale mining claims.
- **Detection:** on held-out PRs, checker produces acceptable precision and opportunity coverage. If not, restrict to record-only and deterministic checks.
- **Effectiveness:** if prospective sample is underpowered or errors dominate, report feasibility and avoid a prevention claim; continue only with a revised experiment plan.
