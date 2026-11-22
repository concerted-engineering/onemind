# Contributing to OneMind

Thank you for your interest in contributing to **OneMind** (Apache-2.0).

## Ground Rules

- **Protocol stability:** The current record protocol is `0.1-draft` (`experimental`). Changes to `spec/record.schema.json` must keep `src/onemind/protocol/record.schema.json` byte-for-byte identical and update conformance fixtures/tests.
- **Security & governance invariants:** Never introduce implicit activation of imported docs/candidates, arbitrary code execution from records, or silent fallback on unknown schema versions or authority modes.
- **Offline-first & minimal dependencies:** Local M0/M1 commands must remain 100% offline with zero telemetry and minimal runtime dependencies.
- **License & DCO:** All contributions are licensed under Apache-2.0. Please include a `Signed-off-by:` trailer (Developer Certificate of Origin) on commits.

## Local Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Verification Before Opening a PR

Run all checks locally:

```bash
ruff check src tests
mypy src/onemind
pytest -v
./scripts/demo.sh
```
