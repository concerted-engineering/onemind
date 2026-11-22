# OneMind Offline API Compatibility Demo

This self-contained, offline example demonstrates the complete OneMind local developer loop (**Scan → Inspect → Propose → Preview → Validate**) without an account, API key, model download, hosted service, or daemon.

> **Important:** All governance decisions in `.onemind/records/governance_decision/` are explicitly labeled `local-owner-assertion` (`verified_repository_approval: false`). They illustrate local advisory workflows and do **not** represent verified repository governance or empirical engineering improvement.

## Contents

- `docs/adr/0001-public-api-response-compatibility.md` — Architecture Decision Record stating backward-compatibility requirements for `/v1/orders` responses.
- `AGENTS.md` — Local agent instruction file (treated strictly as unverified source material, never auto-activated).
- `api/schemas/orders_v1_baseline.json` — Established baseline JSON API response schema.
- `api/schemas/orders_v1_breaking.json` — Breaking change removing `currency`, changing `total_cents` from `integer` to `string`, and dropping `customer_id` from `required`.
- `api/schemas/orders_v1_compatible.json` — Corrected compatible change preserving all baseline fields and adding an optional `estimated_delivery_date` field.
- `.onemind/records/` — Canonical immutable records:
  - `evidence/demo-ev-0001.json` — Source-linked evidence pointing to ADR line 14 with SHA-256 excerpt digest.
  - `practice_candidate/demo-cand-0001.json` — Inert practice candidate linked to `demo-ev-0001`.
  - `practice_version/demo-pv-0001.json` — Versioned normative practice (`public-api-response-compat` v1).
  - `application/demo-app-0001.json` — Separately versioned deterministic checker (`builtin:api-compat-json` v0.1.0).
  - `governance_decision/demo-dec-local-pv-0001.json` & `demo-dec-local-app-0001.json` — Clearly labeled `local-owner-assertion` decisions bound to exact RFC 8785 canonical SHA-256 digests.
  - `outcome_event/demo-out-0001.json` — Synthetic `alert` observation (`synthetic: true`).

## Run the walkthrough

From the repository root:

```bash
./scripts/demo.sh
```
