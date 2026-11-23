"""Transport-independent top-level OneMind library API (`query`, `propose`, `resolve`, `observe`)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from onemind.adapters.interfaces import AuthorityVerifier, OutcomeSink, Store
from onemind.adapters.local_files import capture_source_evidence
from onemind.engine import (
    AnnotatedRecord,
    ObservationResult,
    OneMindEngine,
    ProposalResult,
    QueryCriteria,
    QueryResult,
    ResolutionContext,
)
from onemind.governance.policy import AuthorityPolicy
from onemind.governance.projection import ResolutionResult
from onemind.protocol.models import (
    ActorType,
    ImmutableRecord,
    RecordDict,
    RecordKind,
    generate_record_id,
    make_envelope,
    utc_now_iso,
)


def query(
    store: Store,
    criteria: Mapping[str, Any] | QueryCriteria | None = None,
    as_of: str | None = None,
    *,
    policy: AuthorityPolicy | None = None,
    verifier: AuthorityVerifier | None = None,
    allow_local_assertions: bool | None = None,
) -> QueryResult:
    """Transport-independent read-only query returning deeply immutable annotated records."""
    engine = OneMindEngine(store, policy=policy, verifier=verifier)
    return engine.query(criteria, as_of=as_of, allow_local_assertions=allow_local_assertions)


def propose(
    store: Store,
    candidate: Mapping[str, Any] | ImmutableRecord,
    *,
    evidence: Sequence[Mapping[str, Any] | ImmutableRecord] = (),
    write: bool = False,
) -> ProposalResult:
    """Transport-independent inert candidate proposal (dry-run unless write=True)."""
    engine = OneMindEngine(store)
    return engine.propose(candidate, evidence=evidence, write=write)


def propose_from_source(
    store: Store,
    repo_path: Path | str,
    *,
    statement: str,
    evidence_specs: Sequence[str],
    scope_repository: str | None = None,
    scope_paths: Sequence[str] = (),
    scope_languages: Sequence[str] = (),
    scope_conditions: Sequence[str] = (),
    actor_id: str = "local-developer",
    source_author: str | None = None,
    record_id: str | None = None,
    generator_name: str = "onemind-explicit-capture",
    generator_version: str = "0.1.0a1",
    write: bool = False,
) -> ProposalResult:
    """Explicitly capture a source-linked inert PracticeCandidate from real repository files or existing Evidence IDs.

    Always records `capture_mode: "explicit-capture"` and per-line source provenance,
    clearly distinguishing explicit human capture from heuristic scan extraction.
    Never activates practices.
    """
    root = Path(repo_path).resolve()
    existing_by_id = {str(r["record_id"]): r for r in store.list_records()}
    now = utc_now_iso()

    new_evidence: list[RecordDict] = []
    evidence_ids: list[str] = []

    for spec in evidence_specs:
        if spec in existing_by_id:
            if existing_by_id[spec].get("kind") != RecordKind.EVIDENCE:
                raise ValueError(
                    f"Record {spec!r} has kind {existing_by_id[spec].get('kind')!r}, expected 'evidence'."
                )
            evidence_ids.append(spec)
            continue

        ev_rec = capture_source_evidence(
            root,
            spec,
            actor_id=actor_id,
            source_author=source_author,
            created_at=now,
        )
        new_evidence.append(ev_rec)
        evidence_ids.append(str(ev_rec["record_id"]))

    proposed_scope: dict[str, Any] = {
        "repository": scope_repository or root.name,
    }
    if scope_paths:
        proposed_scope["paths"] = list(scope_paths)
    if scope_languages:
        proposed_scope["languages"] = list(scope_languages)
    if scope_conditions:
        proposed_scope["conditions"] = list(scope_conditions)

    cand_id = record_id or generate_record_id("cand")
    candidate = make_envelope(
        kind=RecordKind.PRACTICE_CANDIDATE,
        body={
            "statement": statement,
            "proposed_scope": proposed_scope,
            "evidence_ids": evidence_ids,
            "generator": {
                "name": generator_name,
                "version": generator_version,
                "capture_mode": "explicit-capture",
            },
        },
        actor_type=ActorType.HUMAN,
        actor_id=actor_id,
        record_id=cand_id,
        created_at=now,
    )

    return propose(store, candidate, evidence=new_evidence, write=write)


def resolve(
    store: Store,
    context: Mapping[str, Any] | ResolutionContext,
    as_of: str | None = None,
    *,
    policy: AuthorityPolicy | None = None,
    verifier: AuthorityVerifier | None = None,
    allow_local_assertions: bool | None = None,
) -> ResolutionResult:
    """Transport-independent read-only effective-state resolution."""
    engine = OneMindEngine(store, policy=policy, verifier=verifier)
    return engine.resolve(context, as_of=as_of, allow_local_assertions=allow_local_assertions)


def observe(
    store: Store,
    event: Mapping[str, Any] | ImmutableRecord,
    *,
    batch_id: str | None = None,
    outcome_sink: OutcomeSink | None = None,
) -> ObservationResult:
    """Transport-independent deduplicated outcome observation (never mutates governance)."""
    engine = OneMindEngine(store, outcome_sink=outcome_sink)
    return engine.observe(event, batch_id=batch_id)


__all__ = [
    "AnnotatedRecord",
    "ImmutableRecord",
    "ObservationResult",
    "OneMindEngine",
    "ProposalResult",
    "QueryCriteria",
    "QueryResult",
    "ResolutionContext",
    "ResolutionResult",
    "observe",
    "propose",
    "propose_from_source",
    "query",
    "resolve",
]
