# OneMind

> **Your team learns once. Every agent benefits.**

**License:** Apache-2.0 · **Protocol:** `0.1-draft` (`experimental`) · **Scope:** M0 + M1 + M2A + M2B (local-first OSS core, CLI, read-only GitHub evidence ingestion & recurring-correction discovery/reconciliation)

OneMind is the open-source engineering-intelligence substrate of **Concerted Engineering**. It enables engineering teams to accumulate source-linked **Evidence**, propose inert **PracticeCandidates**, govern versioned **PracticeVersions** and **Applications** through explicit **GovernanceDecisions** and **Conflicts**, and record append-only **OutcomeEvents**—independently of any specific AI model, coding agent, CI system, or hosted service.

OneMind supports the broader Concerted Engineering lifecycle (**Discover → Define → Execute → Verify → Deliver → Learn**) while staying strictly focused on its core role: preserving shared meaning, provenance, authority, and observed outcomes. It is **not** an agent runtime, orchestration framework, or replacement for CI/CD and IAM.

## Key Properties

- **Local-first & offline:** Zero accounts, zero cloud backend, zero API keys, zero model downloads, zero telemetry.
- **Explicit trust boundaries:**
  - Scanning or importing documentation (`AGENTS.md`, `SKILL.md`, ADRs, retrospectives) produces **inert evidence only** and never activates practices.
  - Governance approvals bind to exact record IDs and **RFC 8785 canonical JSON SHA-256** digests.
  - `local-owner-assertion` approvals are explicitly distinguished from `verified-repository-approval` and cannot activate strict repository policies.
  - Candidate conflicts never disable approved practices; unresolved conflicts between approved practices return `ambiguous` rather than guessing.
  - Application scopes cannot exceed governing practice scopes.
  - `onemind preview` runs audited built-in deterministic checkers only—never arbitrary code from records.
- **Immutable Git-backed ledger:** Canonical records live under `.onemind/records/<kind>/<record_id>.json` with trusted-base historical mutation verification (`onemind validate --trusted-base <commit-or-ref>`).

## Quickstart (Local Installation)

Requires Python 3.11+ and Git:

```bash
git clone <repo-url> onemind
cd onemind
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Verify installation:

```bash
onemind --version
onemind --help
```

## Five-Minute Offline Walkthrough

Run the self-contained offline API compatibility demo (**Scan → Inspect → Propose → Preview → Validate**):

```bash
./scripts/demo.sh
```

Or run the five CLI commands directly against [`examples/api-compat-demo/`](examples/api-compat-demo/):

```bash
# 1. Offline read-only scan of ADRs and instruction files
onemind scan examples/api-compat-demo

# 2. Inspect a source document or a canonical record (provenance, lineage, authority)
onemind inspect docs/adr/0001-public-api-response-compatibility.md:14 --repo examples/api-compat-demo
onemind inspect demo-pv-0001 --repo examples/api-compat-demo --allow-local

# 3. Draft an inert PracticeCandidate (dry-run by default; add --write to persist)
onemind propose \
  --repo examples/api-compat-demo \
  --statement "Previously required response fields in /v1/orders must remain required." \
  --evidence docs/adr/0001-public-api-response-compatibility.md:16 \
  --scope-repo examples/api-compat-demo \
  --scope-path "api/schemas/**" \
  --scope-lang json

# 4. Run a deterministic advisory check (genuine failure on breaking schema, genuine pass on compatible schema)
onemind preview \
  --repo examples/api-compat-demo \
  --application demo-app-0001 \
  --base api/schemas/orders_v1_baseline.json \
  --proposed api/schemas/orders_v1_breaking.json \
  --allow-local

onemind preview \
  --repo examples/api-compat-demo \
  --application demo-app-0001 \
  --base api/schemas/orders_v1_baseline.json \
  --proposed api/schemas/orders_v1_compatible.json \
  --allow-local

# 5. Validate schema, digests, governance invariants, and optional trusted-base immutability
onemind validate examples/api-compat-demo
onemind validate . --records-file examples/records.json
```

All commands support `--json` for automation and use deterministic exit codes: `0` (ok/pass), `1` (validated finding/error), `2` (invalid usage/input), `3` (indeterminate/incomplete).

## Logical Python API (Transport-Independent, Immutable Objects)

```python
from onemind import ResolutionContext, propose_from_source, query, resolve
from onemind.governance.policy import AuthorityPolicy
from onemind.storage.git_store import GitRecordStore

store = GitRecordStore("examples/api-compat-demo")

# Read-only resolution returning deeply immutable objects with explicit provenance
res = resolve(
    store,
    ResolutionContext(
        repository="examples/api-compat-demo",
        paths=("api/schemas/orders_v1_breaking.json",),
        languages=("json",),
    ),
    policy=AuthorityPolicy(allow_local_assertions=True),
)
print(res.state, len(res.practices), len(res.applications))

# Explicit dry-run candidate capture linked to an exact file/line anchor (never activates)
draft = propose_from_source(
    store,
    "examples/api-compat-demo",
    statement="Previously required response fields in /v1/orders must remain required.",
    evidence_specs=["docs/adr/0001-public-api-response-compatibility.md:16"],
    scope_repository="examples/api-compat-demo",
    scope_paths=["api/schemas/**"],
    write=False,
)
print(draft.valid, draft.written)
```

## Read-Only GitHub Review-History Ingestion (M2A — Opt-In)

Ingest bounded GitHub PR review submissions, inline review comments, and conversation comments as strictly **inert `Evidence`** plus immutable run-level coverage manifests (`.onemind/ingestions/<run_id>/manifest.json`). Defaults to **dry-run** and **`metadata-only`** capture (raw review bodies and tokens are never printed or committed by default).

```bash
# Offline deterministic fixture demo (zero network, zero credentials)
onemind ingest github \
  --repository example/service-api \
  --store . \
  --since 2026-10-01 \
  --until 2026-11-01 \
  --fixture-file examples/github-ingest-fixture/github_complete.json \
  --json

# Opt-in live public-repo dry-run (bounded limits, unauthenticated or via --token-env GITHUB_TOKEN)
onemind ingest github \
  --repository OWNER/PUBLIC-REPO \
  --store . \
  --since 2026-10-01 \
  --until 2026-11-01 \
  --max-prs 25 \
  --max-pages 5
```

See [`examples/github-ingest-fixture/README.md`](examples/github-ingest-fixture/README.md) and [`docs/decisions/0002-m2a-github-evidence-ingestion.md`](docs/decisions/0002-m2a-github-evidence-ingestion.md) for the full offline walkthrough, edit/re-ingestion behavior, and privacy controls.

## Recurring Correction Discovery, Reconciliation & Review Digest (M2B — Read-Only Default)

Discover recurring corrections from explicitly opted-in redacted evidence using deterministic rules and independent-PR occurrence counting (never inflating support from repeated comments or edits in the same PR), reconcile against existing candidates/practices without altering approved state, and generate a human-facing review digest:

```bash
# 1. Read-only discovery (independent PR counting; dry-run by default)
onemind discover --repo . --source github --min-independent-prs 2 --output ./proposals.json --json

# 2. Scope-aware reconciliation & inert conflict reporting (non-interfering)
onemind reconcile --repo . --candidates-file ./proposals.json --json

# 3. Render a human-facing Markdown review digest (never creates a PR or approves practices)
onemind review-digest --repo . --candidates-file ./proposals.json --output ./onemind-review.md
```

See [`docs/decisions/0003-m2b-discovery-clustering-reconciliation.md`](docs/decisions/0003-m2b-discovery-clustering-reconciliation.md) and the offline synthetic benchmark corpora under [`examples/discovery-gold-corpus/`](examples/discovery-gold-corpus/) and [`examples/discovery-heldout-corpus/`](examples/discovery-heldout-corpus/).

## Generic Workflow Composability

OneMind defines minimal interfaces in [`src/onemind/adapters/interfaces.py`](src/onemind/adapters/interfaces.py) (`SourceAdapter`, `Extractor`, `AuthorityVerifier`, `Store`, `ApplicationAdapter`, `OutcomeSink`, `Reporter`). Two unrelated synthetic fixtures under [`examples/workflow-fixtures/`](examples/workflow-fixtures/)—a Markdown engineering retrospective (`MarkdownRetroAdapter`) and a structured JSON observation log (`JsonObservationAdapter`)—demonstrate identical ingestion, provenance preservation, non-authority, unified querying/resolution, and independent removability without coupling OneMind to any external workflow engine.

## Documentation

- [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) — Current M0 + M1 implementation status, verified capabilities, and limitations
- [`spec/01-record-protocol.md`](spec/01-record-protocol.md) — Draft record protocol (`0.1-draft` experimental)
- [`spec/record.schema.json`](spec/record.schema.json) — JSON Schema 2020-12 structural schema
- [`docs/architecture.md`](docs/architecture.md) — M0 + M1 architecture and replay model
- [`docs/security.md`](docs/security.md) — Security invariants, threat boundaries, and current limitations
- [`docs/decisions/0001-m0-m1-protocol-and-trust-boundaries.md`](docs/decisions/0001-m0-m1-protocol-and-trust-boundaries.md) — M0 + M1 implementation ADR
- [`docs/decisions/0002-m2a-github-evidence-ingestion.md`](docs/decisions/0002-m2a-github-evidence-ingestion.md) — M2A GitHub evidence ingestion & coverage manifest ADR
- [`docs/decisions/0003-m2b-discovery-clustering-reconciliation.md`](docs/decisions/0003-m2b-discovery-clustering-reconciliation.md) — M2B discovery, independent-PR clustering & reconciliation ADR
- [`docs/M2_BLOCKERS.md`](docs/M2_BLOCKERS.md) — M2A/M2B completion status and remaining M2C/M2D blockers
- [`CONTRIBUTING.md`](CONTRIBUTING.md) · [`SECURITY.md`](SECURITY.md) · [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) · [`LICENSE`](LICENSE)

## Testing & Quality Checks

```bash
ruff check src tests
mypy src/onemind
pytest -v
./scripts/demo.sh
```
