# Engineering Retrospective — Sprint 42 (Synthetic Fixture)

- Sprint: sprint-42
- Facilitator: engineering-lead
- Date: 2026-11-19

## What happened

Two release hotfixes this sprint stemmed from database migrations modifying non-null columns without a dual-write backfill window.

## Follow-up practice proposals

- [ACTION] Database migrations MUST NOT drop or narrow existing columns in the same release as application code removal.
- [PRACTICE] Every schema migration touching production tables MUST include a reversible rollback note in its PR description.

> Synthetic workflow fixture for OneMind adapter composability tests. Contains no governance authority.
