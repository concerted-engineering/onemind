#!/usr/bin/env bash
set -euo pipefail

# OneMind offline five-minute walkthrough:
# Scan -> Inspect -> Propose (dry-run) -> Preview (fail & pass) -> Validate
# Requires no network access, API key, model download, cloud account, or daemon.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_DIR="${ROOT_DIR}/examples/api-compat-demo"

if command -v onemind >/dev/null 2>&1; then
  ONEMIND_CMD=(onemind)
else
  ONEMIND_CMD=(python3 -m onemind)
fi

echo "======================================================================"
echo "OneMind Offline Demo (Protocol 0.1-draft — experimental)"
echo "Demo repository: ${DEMO_DIR}"
echo "======================================================================"

echo ""
echo "--- Step 1: SCAN (offline, read-only inventory of ADRs & instructions) ---"
"${ONEMIND_CMD[@]}" scan "${DEMO_DIR}"

echo ""
echo "--- Step 2a: INSPECT source ADR (unverified source document) ---"
"${ONEMIND_CMD[@]}" inspect "docs/adr/0001-public-api-response-compatibility.md:14" --repo "${DEMO_DIR}"

echo ""
echo "--- Step 2b: INSPECT versioned practice (strict vs --allow-local) ---"
"${ONEMIND_CMD[@]}" inspect demo-pv-0001 --repo "${DEMO_DIR}"
echo ""
echo "(With explicit --allow-local flag — still visibly local-owner-assertion:)"
"${ONEMIND_CMD[@]}" inspect demo-pv-0001 --repo "${DEMO_DIR}" --allow-local

echo ""
echo "--- Step 3: PROPOSE inert PracticeCandidate (dry-run default, no files written) ---"
"${ONEMIND_CMD[@]}" propose \
  --repo "${DEMO_DIR}" \
  --statement "Previously required response fields in /v1/orders must remain required." \
  --evidence "docs/adr/0001-public-api-response-compatibility.md:16" \
  --scope-repo "examples/api-compat-demo" \
  --scope-path "api/schemas/**" \
  --scope-lang "json"

echo ""
echo "--- Step 4a: PREVIEW deterministic checker on BREAKING schema change (expected exit 1) ---"
set +e
"${ONEMIND_CMD[@]}" preview \
  --repo "${DEMO_DIR}" \
  --application demo-app-0001 \
  --base api/schemas/orders_v1_baseline.json \
  --proposed api/schemas/orders_v1_breaking.json \
  --allow-local
BREAK_EXIT=$?
set -e
echo "Breaking change check exit code: ${BREAK_EXIT} (expected: 1)"
if [ "${BREAK_EXIT}" -ne 1 ]; then
  echo "ERROR: Expected breaking schema check to exit with code 1, got ${BREAK_EXIT}" >&2
  exit 1
fi

echo ""
echo "--- Step 4b: PREVIEW deterministic checker on COMPATIBLE schema change (expected exit 0) ---"
"${ONEMIND_CMD[@]}" preview \
  --repo "${DEMO_DIR}" \
  --application demo-app-0001 \
  --base api/schemas/orders_v1_baseline.json \
  --proposed api/schemas/orders_v1_compatible.json \
  --allow-local

echo ""
echo "--- Step 5: VALIDATE canonical records & governance invariants ---"
"${ONEMIND_CMD[@]}" validate "${DEMO_DIR}"

echo ""
echo "======================================================================"
echo "Demo complete! All steps ran 100% offline with zero worktree writes."
echo "======================================================================"
