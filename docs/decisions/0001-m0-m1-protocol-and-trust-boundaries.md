# Decision 0001: M0 + M1 Protocol, Canonicalization, and Trust Boundary Choices

- **Status:** Accepted (for `0.1-draft` experimental implementation)
- **Date:** 2026-11-22
- **Scope:** `src/onemind/`

## Context

Implementing OneMind M0 (protocol + governance + storage) and M1 (local CLI + offline demo + generic adapter composability) required resolving several subtle engineering and security details conservatively.

## Decisions

1. **Full RFC 8785 (JCS) canonicalization in pure Python**
   - Implemented UTF-16 code-unit object key sorting (`key.encode("utf-16-be")`) and ECMA-262 shortest round-trip IEEE 754 number formatting in `src/onemind/protocol/canonical.py` with zero third-party dependencies.
   - Verified against RFC 8785 Section 3.2.2/3.2.3 vectors and against all digests in `examples/records.json`.

2. **Bundled JSON Schema kept strictly in sync with `spec/record.schema.json`**
   - Packaged `src/onemind/protocol/record.schema.json` for wheel installation and added a test enforcing byte-for-byte identity with `spec/record.schema.json`.
   - Added a custom strict RFC 3339 `date-time` `FormatChecker` so timezone-naive or malformed timestamps fail validation without external date-parsing packages.

3. **Conservative static scope containment**
   - `is_scope_subset()` only approves application applicability when repository matches exactly, every application path glob is statically covered by a practice path glob, application languages are a subset of practice languages, and no required practice conditions are dropped.
   - Any unprovable glob containment fails safely (`APPLICATION_SCOPE_OVERREACH`).

4. **Strict separation of `local-owner-assertion` vs `verified-repository-approval`**
   - Default `AuthorityPolicy` sets `allow_local_assertions=False` so local assertions never silently activate repository-governed practices.
   - Local advisory commands (`inspect --allow-local`, `preview --allow-local`) opt in explicitly and always display `verified_repository_approval: false` and `authority_class: local-owner-assertion`.

5. **Built-in checker registry only (no dynamic script loading)**
   - `onemind preview` only executes explicitly audited built-in checkers registered in `src/onemind/checks/registry.py`. Any unrecognized `detection.implementation` identifier returns `indeterminate` (exit code `3`).

6. **Generic workflow composability without external framework coupling**
   - Implemented two independent synthetic adapters (`MarkdownRetroAdapter` and `JsonObservationAdapter`) over the common `SourceAdapter`/`Extractor` protocols, with zero runtime dependency on external coding-agent or workflow frameworks.
