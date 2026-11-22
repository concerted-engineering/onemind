# RFC-0001: OneMind Open Ecosystem and Commercial Boundary

**Status:** Proposed for discussion, not ratified.  
**Owner:** TBD; name/mark availability and project ownership must be verified prior to release.  
**Guiding statement:** *Your team learns once. Every agent benefits.*

## Summary

OneMind's value is continuity of **evidence → approved decisions → applications → measured outcomes**, not another hosted memory index or a new agent runtime. The core format and engine must be independent of model provider, repository host, toolchain, and commercial platform. A maintainer should be able to use the complete learning/governance loop without OneMind Cloud.

This RFC adopts the distribution advantages of Terraform-style CLI + ecosystem + optional managed operations and the vendor-neutral specification practices of OpenTelemetry. It explicitly rejects future relicensing of the core into a non-OSS license as a planned monetization tactic.

## 1. The two adoption markets

**Developer-led:** solo contributors, maintainers, small teams. They need an immediate result without account creation. Distribution via local CLI, package indexes, documented GitHub Action, example repos, and agent adapters. Trust is built by read-only defaults and auditable data use.

**Platform-led:** larger teams and enterprises. They need repeatability, identity federation, policy authority boundaries, cross-repo scope, synchronized storage, audit retention, data classification, admin controls and support. These are operational complexities, not reasons to withhold the core semantics.

The sequence is developer utility → team reuse → third-party integrations → operational enterprise demand. Do not launch a commercial service before demonstrating retention among standalone OSS teams.

## 2. Four portable logical interfaces

- `query(context, as_of)` — read source-linked practices and evidence with provenance. Source truth and authority are distinct.
- `propose(candidate)` — submit an inert suggestion from any adapter (review, ADR, incident, agent feedback).
- `resolve(context, as_of)` — return effective governed practices and application metadata or an explicit ambiguous/indeterminate result; NEVER grant external execution authority.
- `observe(event)` — submit authenticated execution/effect observations to the append-only ledger. Observations do not rewrite policy.

The reference Python API/CLI is first. HTTP, MCP resources/tools and event streams are **optional transport adapters**, not a replacement for the data model. Authentication and tenancy must be handled by the deployment environment; a transport cannot relax governance semantics.

## 3. Standardize little, extend deliberately

**Normative v0.1:** record envelope, stable IDs/references, seven record types, scope semantics for one repo, decision authorization proofs, effective-state projection, event lineage, mutation checks, conformance fixtures.

**Non-normative v0.1:** GitHub connector, Markdown parser, braindump-derived extractor, human-facing CLI and internal indexes.

**Deferred:** generalized semantic constraints across products, cross-org distributed consensus, JSON-LD projection guarantees, agent skill orchestration, predictive recommendation algorithms, hosted tenancy and cross-organization authority.

Use a compatibility matrix and explicit status labels: `experimental`, `development`, `stable`, `deprecated`. Do not call an experimental record draft a universal industry standard before independent implementations exist. Version schemas separately from engine releases. Unknown authorization semantics must fail safely.

## 4. Adapter/plugin architecture

| Surface | Input/output contract | First implementation | Next |
|---|---|---|---|
| SourceAdapter | Immutable evidence with completeness metadata | Files, GitHub PR reviews | GitLab, ADR, incidents |
| Extractor | Source-linked inert candidate + generator provenance | Manual/import + optional review extractor | Additional local and hosted LLMs |
| AuthorityVerifier | Claims + external proof → verified decision state | Protected GitHub repository | SSO, delegated enterprise policy |
| Store | Append, snapshot, reference resolution, trusted base | Git files | SQLite, Postgres, object store |
| ApplicationAdapter | Approved practice → validated check/view | Deterministic GitHub preflight | Lint, test, agent context |
| OutcomeSink | Deduplicated immutable event batches | Git ledger | Managed event storage |
| Reporter | Pure recompute from pinned snapshot | Local CLI report | Dashboards |

All production adapters require threat-model fixtures and a provenance contract. Never allow a plugin to silently mark extracted content as approved.

### Workflow reference profiles (RFC-0002)

External engineering workflow frameworks are **composable producers and consumers**, not OneMind runtimes. A generic WorkflowAdapter may bundle SourceAdapter/Extractor/ApplicationAdapter/OutcomeSink capabilities, but MUST NOT create a new authorization path or become part of the normative record format. **HumanMaxxing** is the first reference profile for the AI coding-workflow angle (planning, review, ship, retro); see [`06-composable-workflows-humanmaxxing.md`](06-composable-workflows-humanmaxxing.md). In v0.1 this is a read-only import and conformance fixture, not a runtime dependency or endorsed upstream integration. An independent second workflow fixture must pass the same tests.

## 5. Practice packs and interoperability

A public pack bundles **illustrative practice definitions, explicit scopes, checker implementations, validation fixtures, compatibility metadata, and license**. It MUST NOT carry transferable authorizations. Importing a community pack creates *candidates*; the receiving team decides whether to approve them. A generic pack may define API compatibility checker semantics but cannot know an adopter's release governance or risk appetite.

Publishing gates:

- isolated, version-pinned implementation and reproducible fixtures;
- no arbitrary shell execution as part of passive scanning;
- package signing/provenance and dependency checks when available;
- limited privileges, data classification and network policy declaration;
- automated tests for negative and positive cases;
- security-contact and removal process;
- optional README badges only if meaningful and not inflated.

**Important:** existing `AGENTS.md` and `SKILL.md` standards are portable presentation/agent-interaction outputs, not competing authority systems. In the randomized P2 experiment they remain unmerged previews. Outside an experiment, outputs can be generated only after explicit opt-in with scope/size controls.

## 6. OSS and OneMind Cloud boundary

### Always open (proposed commitment)

Apache-2.0 engine, CLI, record spec, schemas, validator, governance semantics, basic local and Git store, approved practice lifecycle, local preflight, outcome reporting, conformance suite, standard integrations, and the ability to run the full loop self-hosted. Core data export/import has no paywall. No mandatory telemetry or account.

### Potential paid operational conveniences

Managed multi-repository sync, enterprise SSO/SCIM, centralized administration, audit storage/retention operations, encrypted backup, high availability, hosted analytics, enterprise integrations, support/SLA and user-friendly organization UX. Self-hosted OSS may achieve equivalent outcomes through user-operated components; commercial features must not alter protocol truth or portability.

The commercial value proposition is **pay to operate easily at scale**, not **pay to access your own organizational knowledge**.

### Licensing and stewardship

- Publish code and spec under Apache-2.0 as presently planned; retain copyright notices and third-party attribution.
- A governance charter should state that Apache-2.0 core remains permissively licensed. Since long-term licensing guarantees depend on ownership/governance, consider a neutral standards home once contributors/implementations justify it.
- Prefer DCO/sign-off with clear contribution documentation. Do not require copyright assignment as a condition for community participation without a documented reason.
- Publish trademark and compatibility-claim policy separately from code license; names require clearance before public branding.
- Publish security policy, disclosure channel, Code of Conduct, maintenance policy and RFC process.
- Do not promise independent foundation governance before a real outside community exists.

## 7. Anti-capture mechanisms

- The public spec may be implemented without the OneMind client.
- Conformance tests must be usable by competing vendors.
- A community implementation is not penalized for not using OneMind Cloud.
- No hidden server calls in local commands.
- No proprietary-only record fields required for reading/extracting core decisions and outcomes.
- Export preserves IDs, provenance, version chains, application relationships and outcome lineage.
- A provider can query approved practices but cannot bypass OneMind's local governance model merely by supplying a commercial signature.

## 8. Community and viral adoption loops

**First-use loop:** account-free scan finds relevant existing source-backed conventions → maintainer captures an approved practice → useful deterministic check validates it on a change. No fabricated "AI magically learned" claims.

**PR loop:** source-linked advisory findings explain why a requirement exists. Teammates see useful evidence, not product promotion or noisy comments.

**Pack loop:** maintainers publish reusable checkers and test fixtures without disclosing their team's private evidence or importing another organization's authority.

**Integration loop:** agent/tool authors consume the same protocol through a well-documented SDK. Every adapter expands ecosystem utility without becoming a privileged policy administrator.

**Evidence loop:** teams optionally publish redacted, reproducible case studies (including null findings). Avoid league tables of "AI productivity" across incomparable teams.

## 9. Community growth measurements

**North star:** number of repos with at least one genuinely used, independently authorized practice that produced a verified check or outcome observation in the previous 28 days (reported only from opt-in aggregate stats or community research; never collected by covert telemetry).

Track: time-to-first-useful-insight, onboarding completion, weekly active repositories by cohort, false-alert burden, retention, cross-agent reuse, outside pull requests merged, independent adapter implementations, pack reuse, and independently reproduced experiments. Stars/downloads can inform discovery, not quality claims.

## 10. Concerted Engineering alignment

OneMind supports continuity across Discover → Define → Execute → Verify → Deliver → Learn without owning all six systems. v0.1 deliberately implements a narrow review-learning loop. Future decisions, requirements and operational observations may enter as source evidence and governed records, but no new universal `intent` ontology should be invented before demonstrating user demand and stable semantics.

- **OneMind:** what the team knows, decided, applies, and learned.
- **Hardknock:** empirical failure-derived experiences that can provide candidate evidence.
- **KedgeFlow:** separate policy/control plane for authorizing consequential actions; a OneMind approval is never an execution grant.
- **Concerted Engineering:** public operating methodology, not dependent on OneMind deployment.

Integrations must be optional and evidence-oriented, with explicit trust/ownership boundaries.

## 11. Community governance path

**Stage 0 (founder-led):** public RFCs, decision log, lightweight review times, transparent roadmap and clear maintainer ownership. **Stage 1:** named external maintainers with documented commit/release authority after demonstrated contribution. **Stage 2:** independent spec governance or foundation stewardship only if real multi-vendor adoption and resources justify it. All stages keep the reference implementation replaceable.

## 12. Critical unresolved questions

1. What is the first external repository with permission to publish an evidence-backed demo?
2. How will authoritative scope overlap and exceptions be adjudicated without pretending that all organizations share one hierarchy?
3. Which schema fields are truly necessary for an independent implementation to pass conformance?
4. Does developer value arise from discovery, enforcement, or audit explanation? Instrument opt-in usability research before expanding scope.
5. Which commercial operations features are genuinely demanded versus imagined?
6. How will incident/ADR-source connectors evolve without making source content itself normative?

## References

[Terraform style CLI/ecosystem conceptual model](https://developer.hashicorp.com/terraform); [OpenTelemetry stability conventions](https://opentelemetry.io/docs/specs/semconv/how-to-write-conventions/); [Agent Skills](https://agentskills.io/specification); [AGENTS.md](https://agents.md/); [W3C PROV-O](https://www.w3.org/TR/prov-o/).
