"""Transport-independent logical domain operations: query, propose, resolve, observe."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from onemind.adapters.interfaces import AuthorityVerifier, OutcomeSink, Store
from onemind.governance.authority import DefaultAuthorityVerifier
from onemind.governance.policy import AuthorityPolicy
from onemind.governance.projection import (
    EffectiveState,
    ResolutionResult,
    project_effective_state,
    resolve_context,
)
from onemind.protocol.canonical import canonical_sha256
from onemind.protocol.models import (
    COMPUTATION_VERSION,
    AuthorityClass,
    RecordDict,
    RecordKind,
    parse_rfc3339,
    utc_now_iso,
)
from onemind.protocol.scope import scope_matches_context
from onemind.protocol.validator import ValidationReport, validate_records


@dataclass(frozen=True)
class AnnotatedRecord:
    """A record returned by query() annotated with explicit authority/provenance."""

    record: RecordDict
    record_sha256: str
    authority_class: AuthorityClass
    lifecycle: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record.get("record_id"),
            "kind": self.record.get("kind"),
            "record_sha256": self.record_sha256,
            "authority_class": self.authority_class.value,
            "lifecycle": self.lifecycle,
            "record": self.record,
        }


@dataclass(frozen=True)
class QueryResult:
    """Result of logical query(criteria, as_of)."""

    as_of: str
    commit_ref: str | None
    computation_version: str
    records: list[AnnotatedRecord]

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "commit_ref": self.commit_ref,
            "computation_version": self.computation_version,
            "count": len(self.records),
            "records": [r.to_dict() for r in self.records],
        }


@dataclass(frozen=True)
class ProposalResult:
    """Result of logical propose(candidate): always inert, dry-run unless write=True."""

    written: bool
    valid: bool
    candidate: RecordDict
    evidence: list[RecordDict] = field(default_factory=list)
    written_record_ids: list[str] = field(default_factory=list)
    validation: ValidationReport | None = None
    note: str = "Practice candidates are always inert and never activate automatically."

    def to_dict(self) -> dict[str, Any]:
        return {
            "written": self.written,
            "valid": self.valid,
            "written_record_ids": self.written_record_ids,
            "candidate": self.candidate,
            "evidence": self.evidence,
            "validation": self.validation.to_dict() if self.validation else None,
            "note": self.note,
        }


@dataclass(frozen=True)
class ObservationResult:
    """Result of logical observe(event): deduplicated append without governance mutation."""

    appended: bool
    deduplicated: bool
    record_id: str
    dedup_key: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "appended": self.appended,
            "deduplicated": self.deduplicated,
            "record_id": self.record_id,
            "dedup_key": self.dedup_key,
        }


class OneMindEngine:
    """Core transport-independent engine implementing query, propose, resolve, observe."""

    def __init__(
        self,
        store: Store,
        *,
        policy: AuthorityPolicy | None = None,
        verifier: AuthorityVerifier | None = None,
        outcome_sink: OutcomeSink | None = None,
    ) -> None:
        self.store = store
        self.policy = policy or AuthorityPolicy()
        self.verifier = verifier or DefaultAuthorityVerifier()
        self.outcome_sink = outcome_sink if outcome_sink is not None else (
            store if isinstance(store, OutcomeSink) else None
        )

    def effective_state(
        self,
        *,
        as_of: str | None = None,
        allow_local_assertions: bool | None = None,
    ) -> EffectiveState:
        eff_policy = (
            self.policy.with_local_assertions(allow_local_assertions)
            if allow_local_assertions is not None
            else self.policy
        )
        return project_effective_state(
            self.store.list_records(),
            as_of=as_of,
            policy=eff_policy,
            verifier=self.verifier,
            commit_ref=self.store.snapshot_commit(),
            ledger_order=self.store.ledger_order(),
        )

    def query(
        self,
        criteria: dict[str, Any] | None = None,
        as_of: str | None = None,
        *,
        allow_local_assertions: bool | None = None,
    ) -> QueryResult:
        """Read-only query returning records annotated with explicit authority labels.

        Supported criteria keys:
          - kinds: list[str]
          - record_ids: list[str]
          - repository: str
          - paths: list[str]
          - languages: list[str]
          - text: str (case-insensitive substring match against statement or snapshot)
          - source_type: str (for evidence records)
        """
        crit = criteria or {}
        eval_as_of = as_of or utc_now_iso()
        eval_dt = parse_rfc3339(eval_as_of)
        eff = self.effective_state(as_of=eval_as_of, allow_local_assertions=allow_local_assertions)

        allowed_kinds = set(crit["kinds"]) if crit.get("kinds") else None
        allowed_ids = set(crit["record_ids"]) if crit.get("record_ids") else None
        text_filter = str(crit["text"]).lower() if crit.get("text") else None
        source_type_filter = str(crit["source_type"]) if crit.get("source_type") else None
        scope_ctx = (
            {
                "repository": crit["repository"],
                "paths": crit.get("paths"),
                "languages": crit.get("languages"),
                "conditions": crit.get("conditions"),
            }
            if crit.get("repository")
            else None
        )

        matched: list[AnnotatedRecord] = []
        for rec in self.store.list_records():
            rec_id = str(rec.get("record_id", ""))
            kind = str(rec.get("kind", ""))

            # Respect as_of filter on created_at when querying historical view
            created_str = rec.get("created_at")
            if isinstance(created_str, str):
                try:
                    if parse_rfc3339(created_str) > eval_dt:
                        continue
                except ValueError:
                    pass

            if allowed_kinds is not None and kind not in allowed_kinds:
                continue
            if allowed_ids is not None and rec_id not in allowed_ids:
                continue

            body = rec.get("body", {})
            if source_type_filter is not None and (
                kind != RecordKind.EVIDENCE or body.get("source_type") != source_type_filter
            ):
                continue

            if scope_ctx is not None:
                rec_scope = body.get("scope") or body.get("proposed_scope") or body.get("applicability")
                if isinstance(rec_scope, dict):
                    if not scope_matches_context(rec_scope, scope_ctx):
                        continue
                elif kind == RecordKind.OUTCOME_EVENT:
                    if body.get("repo_id") != scope_ctx["repository"]:
                        continue
                elif kind == RecordKind.EVIDENCE:
                    # Evidence is repo-linked via source_uri or extensions if present
                    pass
                else:
                    continue

            if text_filter is not None:
                haystack = " ".join(
                    str(body.get(k, ""))
                    for k in ("statement", "redacted_snapshot", "rationale", "source_uri")
                ).lower()
                if text_filter not in haystack:
                    continue

            auth_class, lifecycle = self._classify_record_authority(rec_id, kind, eff)
            matched.append(
                AnnotatedRecord(
                    record=rec,
                    record_sha256=canonical_sha256(rec),
                    authority_class=auth_class,
                    lifecycle=lifecycle,
                )
            )

        return QueryResult(
            as_of=eval_as_of,
            commit_ref=self.store.snapshot_commit(),
            computation_version=COMPUTATION_VERSION,
            records=matched,
        )

    def _classify_record_authority(
        self, rec_id: str, kind: str, eff: EffectiveState
    ) -> tuple[AuthorityClass, str]:
        if kind == RecordKind.EVIDENCE:
            return AuthorityClass.INERT_EVIDENCE, "inert"
        if kind == RecordKind.PRACTICE_CANDIDATE:
            return AuthorityClass.INERT_CANDIDATE, "inert"
        if kind == RecordKind.PRACTICE_VERSION:
            st = eff.practice_states.get(rec_id)
            if st is not None:
                return st.authority_class, st.lifecycle
            return AuthorityClass.UNAPPROVED, "unapproved"
        if kind == RecordKind.APPLICATION:
            st = eff.application_states.get(rec_id)
            if st is not None:
                return st.authority_class, st.lifecycle
            return AuthorityClass.UNAPPROVED, "unapproved"
        if kind == RecordKind.CONFLICT:
            cst = eff.conflict_states.get(rec_id)
            if cst is not None and cst.adjudicated:
                return cst.authority_class, f"adjudicated:{cst.disposition}"
            return AuthorityClass.INERT_EVIDENCE, "unadjudicated"
        if kind == RecordKind.GOVERNANCE_DECISION:
            for v in eff.verification_log:
                if v.decision_record_id == rec_id:
                    return v.authority_class, "effective" if v.effective_for_policy else "ineffective"
            return AuthorityClass.UNAPPROVED, "pending-or-unverified"
        return AuthorityClass.INERT_EVIDENCE, "observed"

    def propose(
        self,
        candidate: RecordDict,
        *,
        evidence: Sequence[RecordDict] = (),
        write: bool = False,
    ) -> ProposalResult:
        """Validate and optionally persist an inert PracticeCandidate (and optional Evidence).

        Never grants rights or activates any practice.
        """
        if candidate.get("kind") != RecordKind.PRACTICE_CANDIDATE:
            raise ValueError("propose() only accepts 'practice_candidate' records.")
        for ev in evidence:
            if ev.get("kind") != RecordKind.EVIDENCE:
                raise ValueError("propose() evidence parameter only accepts 'evidence' records.")

        combined = [*self.store.list_records(), *evidence, candidate]
        report = validate_records(combined)
        if not report.valid:
            return ProposalResult(
                written=False,
                valid=False,
                candidate=candidate,
                evidence=list(evidence),
                validation=report,
            )

        written_ids: list[str] = []
        if write:
            for ev in evidence:
                written_ids.append(self.store.append(ev))
            written_ids.append(self.store.append(candidate))

        return ProposalResult(
            written=write,
            valid=True,
            candidate=candidate,
            evidence=list(evidence),
            written_record_ids=written_ids,
            validation=report,
        )

    def resolve(
        self,
        context: dict[str, Any],
        as_of: str | None = None,
        *,
        allow_local_assertions: bool | None = None,
    ) -> ResolutionResult:
        """Read-only projection of verified effective practices and applicable applications."""
        eff = self.effective_state(as_of=as_of, allow_local_assertions=allow_local_assertions)
        return resolve_context(eff, context)

    def observe(
        self,
        event: RecordDict,
        *,
        batch_id: str | None = None,
    ) -> ObservationResult:
        """Append a deduplicated, immutable OutcomeEvent without altering governance state."""
        if event.get("kind") != RecordKind.OUTCOME_EVENT:
            raise ValueError("observe() only accepts 'outcome_event' records.")
        existing_records = self.store.list_records()
        # Validate event against existing store references (skip duplicate dedup error if identical)
        dedup_key = str(event.get("body", {}).get("dedup_key", ""))
        already_same = False
        for rec in existing_records:
            if (
                rec.get("kind") == RecordKind.OUTCOME_EVENT
                and rec.get("body", {}).get("dedup_key") == dedup_key
            ):
                if canonical_sha256(rec["body"]) == canonical_sha256(event["body"]):
                    already_same = True
                    return ObservationResult(
                        appended=False,
                        deduplicated=True,
                        record_id=str(rec["record_id"]),
                        dedup_key=dedup_key,
                    )
                raise ValueError(
                    f"Conflicting outcome_event for existing dedup_key {dedup_key!r}."
                )

        if not already_same:
            report = validate_records([*existing_records, event])
            if not report.valid:
                msg = "; ".join(f"[{e.code}] {e.message}" for e in report.errors)
                raise ValueError(f"Invalid outcome_event: {msg}")

        if self.outcome_sink is not None:
            appended, rec_id = self.outcome_sink.record_outcome(event, batch_id=batch_id)
            return ObservationResult(
                appended=appended,
                deduplicated=not appended,
                record_id=rec_id,
                dedup_key=dedup_key,
            )

        rec_id = self.store.append(event)
        return ObservationResult(
            appended=True,
            deduplicated=False,
            record_id=rec_id,
            dedup_key=dedup_key,
        )
