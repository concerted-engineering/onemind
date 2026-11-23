#!/usr/bin/env bash
set -euo pipefail

# OneMind offline five-minute walkthrough:
# Scan -> Inspect -> Propose (dry-run + isolated write) -> Preview (fail & pass) -> Validate
# Requires no network access, API key, model download, cloud account, or daemon.
#
# DISCLAIMER: All governance records in examples/api-compat-demo/ are explicit
# local-owner-assertion examples (verified_repository_approval=false). This demo
# does NOT claim enterprise enforcement or empirical engineering improvement.

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
echo "Notice: Local-only assertions only; NOT enterprise enforcement or"
echo "        an empirical measurement of engineering improvement."
echo "======================================================================"

echo ""
echo "--- Step 1: SCAN (offline, read-only inventory with per-line provenance) ---"
"${ONEMIND_CMD[@]}" scan "${DEMO_DIR}"

echo ""
echo "--- Step 2a: INSPECT source ADR (per-line provenance; unverified source) ---"
"${ONEMIND_CMD[@]}" inspect "docs/adr/0001-public-api-response-compatibility.md:14-16" --repo "${DEMO_DIR}"

echo ""
echo "--- Step 2b: INSPECT versioned practice (strict policy vs --allow-local) ---"
"${ONEMIND_CMD[@]}" inspect demo-pv-0001 --repo "${DEMO_DIR}"
echo ""
echo "(With explicit --allow-local — visibly labeled local-owner-assertion, verified_repo_approval=False:)"
"${ONEMIND_CMD[@]}" inspect demo-pv-0001 --repo "${DEMO_DIR}" --allow-local

echo ""
echo "--- Step 3: PROPOSE inert PracticeCandidate from real ADR line (dry-run default) ---"
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
echo "--- Step 6: ISOLATED TMP DEMO — atomic explicit capture + trusted-base Git check ---"
TMP_DEMO="$(mktemp -d)"
trap 'rm -rf "${TMP_DEMO}"' EXIT
cp -R "${DEMO_DIR}/." "${TMP_DEMO}/"
git -C "${TMP_DEMO}" init -q -b main
git -C "${TMP_DEMO}" config user.email "demo@example.invalid"
git -C "${TMP_DEMO}" config user.name "OneMind Demo"
git -C "${TMP_DEMO}" add .
git -C "${TMP_DEMO}" commit -q -m "Establish trusted base for demo"
TRUSTED_SHA="$(git -C "${TMP_DEMO}" rev-parse HEAD)"

"${ONEMIND_CMD[@]}" propose \
  --repo "${TMP_DEMO}" \
  --statement "Previously required response fields in /v1/orders must remain required." \
  --evidence "docs/adr/0001-public-api-response-compatibility.md:16" \
  --scope-repo "examples/api-compat-demo" \
  --scope-path "api/schemas/**" \
  --scope-lang "json" \
  --record-id "demo-cand-explicit-0002" \
  --write

"${ONEMIND_CMD[@]}" inspect "demo-cand-explicit-0002" --repo "${TMP_DEMO}"
"${ONEMIND_CMD[@]}" validate "${TMP_DEMO}" --trusted-base "${TRUSTED_SHA}"

echo ""
echo "======================================================================"
echo "Demo complete! Ran 100% offline with zero modifications to repo worktree."
echo "======================================================================"
