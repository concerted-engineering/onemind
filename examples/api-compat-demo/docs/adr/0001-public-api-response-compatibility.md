# ADR-0001: Public API Response Backward Compatibility

- **Status:** Accepted
- **Date:** 2026-11-15
- **Owner:** api-platform-maintainers
- **Scope:** `api/schemas/**`

## Context

External SDKs and partner integrations deserialize JSON responses from the public `/v1/orders` API. Repeated code reviews have caught pull requests that accidentally removed or renamed response fields or changed field types without a versioned migration.

## Decision

Public API response schemas MUST NOT remove established fields or change existing field types incompatibly within a major API version.

Previously required response fields MUST remain required so existing clients do not encounter unexpected missing keys.

Additive optional fields MAY be introduced without bumping the major API version.

## Consequences

- Breaking schema removals require a new major version (e.g., `/v2/orders`) and explicit deprecation review.
- Discovering this ADR in a repository provides source evidence only; activating automated checks requires an explicitly authored `PracticeVersion`, a separately versioned `Application`, and qualifying `GovernanceDecision` records.
