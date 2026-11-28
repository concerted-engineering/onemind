# M2A Offline GitHub Review-History Ingestion Fixtures

These synthetic fixtures reproduce the complete M2A read-only GitHub ingestion workflow **100% offline** (zero network requests, zero GitHub credentials).

## Offline Fixture Walkthrough

```bash
# 1. Dry-run complete ingestion (default: metadata-only, zero files written)
onemind ingest github \
  --repository example/service-api \
  --store . \
  --since 2026-10-01 \
  --until 2026-11-01 \
  --fixture-file examples/github-ingest-fixture/github_complete.json \
  --json

# 2. Persist into a temporary store with explicit redacted-excerpt opt-in
TMP_STORE="$(mktemp -d)"
onemind ingest github \
  --repository example/service-api \
  --store "${TMP_STORE}" \
  --since 2026-10-01 \
  --until 2026-11-01 \
  --capture-policy redacted-excerpt \
  --fixture-file examples/github-ingest-fixture/github_complete.json \
  --write

# 3. Re-ingest with an edited comment + HTTP 403 permission gap (exits 3: incomplete)
onemind ingest github \
  --repository example/service-api \
  --store "${TMP_STORE}" \
  --since 2026-10-01 \
  --until 2026-11-01 \
  --capture-policy redacted-excerpt \
  --fixture-file examples/github-ingest-fixture/github_edited_and_partial.json \
  --write \
  --json || echo "Exit code: $? (expected 3 for incomplete coverage)"

# 4. Validate all persisted Evidence records and IngestionRun manifests
onemind validate "${TMP_STORE}"
rm -rf "${TMP_STORE}"
```

## Opt-In Live Public Repository Example (Read-Only, Dry-Run Default)

Requires explicit user invocation and network access; never runs in automated tests:

```bash
# Unauthenticated public repository dry-run (metadata-only default, bounded limits)
onemind ingest github \
  --repository OWNER/PUBLIC-REPO \
  --store . \
  --since 2026-10-01 \
  --until 2026-11-01 \
  --max-prs 10 \
  --max-pages 2

# Authenticated with a least-privilege read-only token via env var (never pass token on CLI)
export GITHUB_TOKEN="github_pat_..."  # read-only Contents/Pull Requests/Issues scope
onemind ingest github \
  --repository OWNER/REPO \
  --store . \
  --since 2026-10-01 \
  --until 2026-11-01 \
  --token-env GITHUB_TOKEN \
  --max-prs 25 \
  --max-pages 5
```
