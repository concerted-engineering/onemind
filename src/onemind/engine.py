"""Transport-independent logical domain operations using deeply immutable objects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
    ImmutableRecord,
    RecordDict,
    RecordKind,
    freeze_value,
    parse_rfc3339,
    unfreeze_value,
    utc_now_iso,
)
from onemind.protocol.scope import scope_matches_context
from onemind.protocol.validator import ValidationReport, validate_records


@dataclass(frozen=True)
class QueryCriteria:
    """Immutable criteria object for transport-independent query()."""

    kinds: tuple[str, ...] = ()
    record_ids: tuple[str, ...] = ()
    repository: str | None = None
    paths: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    text: str | None = None
    source_type: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | QueryCriteria | None) -> QueryCriteria:
        if data is None:
            return cls()
        if isinstance(data, QueryCriteria):
            return data
        return cls(
            kinds=tuple(str(k) for k in (data.get("kinds") or ())),
            record_ids=tuple(str(r) for r in (data.get("record_ids") or ())),
            repository=str(data["repository"]) if data.get("repository") else None,
            paths=tuple(str(p) for p in (data.get("paths") or ())),
            languages=tuple(str(lang) for lang in (data.get("languages") or ())),
            conditions=tuple(str(c) for c in (data.get("conditions") or ())),
            text=str(data["text"]) if data.get("text") else None,
            source_type=str(data["source_type"]) if data.get("source_type") else None,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.kinds:
            out["kinds"] = list(self.kinds)
        if self.record_ids:
            out["record_ids"] = list(self.record_ids)
        if self.repository:
            out["repository"] = self.repository
        if self.paths:
            out["paths"] = list(self.paths)
        if self.languages:
            out["languages"] = list(self.languages)
        if self.conditions:
            out["conditions"] = list(self.conditions)
        if self.text:
            out["text"] = self.text
        if self.source_type:
            out["source_type"] = self.source_type
        return out


@dataclass(frozen=True)
class ResolutionContext:
    """Immutable context object for transport-independent resolve()."""

    repository: str
    paths: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()

    def to_mapping(self) -> Mapping[str, Any]:
        out: dict[str, Any] = {"repository": self.repository}
        if self.paths:
            out["paths"] = list(self.paths)
        if self.languages:
            out["languages"] = list(self.languages)
        if self.conditions:
            out["conditions"] = list(self.conditions)
        frozen: Mapping[str, Any] = freeze_value(out)
        return frozen


@dataclass(frozen=True)
class AnnotatedRecord:
    """Deeply immutable record returned by query() annotated with explicit authority/provenance."""

    record: ImmutableRecord
    record_sha256: str
    authority_class: AuthorityClass
    lifecycle: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record.record_id,
            "kind": self.record.kind,
            "record_sha256": self.record_sha256,
            "authority_class": self.authority_class.value,
            "lifecycle": self.lifecycle,
            "record": self.record.to_dict(),
        }


@dataclass(frozen=True)
class QueryResult:
    """Immutable result of logical query(criteria, as_of)."""

    as_of: str
    commit_ref: str | None
    computation_version: str
    records: tuple[AnnotatedRecord, ...]

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
    """Immutable result of logical propose(candidate): always inert, dry-run unless write=True."""

    written: bool
    valid: bool
    candidate: Mapping[str, Any]
    evidence: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    written_record_ids: tuple[str, ...] = field(default_factory=tuple)
    validation: ValidationReport | None = None
    note: str = "Practice candidates are always inert and never activate automatically."

    def to_dict(self) -> dict[str, Any]:
        return {
            "written": self.written,
            "valid": self.valid,
            "written_record_ids": list(self.written_record_ids),
            "candidate": unfreeze_value(self.candidate),
            "evidence": [unfreeze_value(e) for e in self.evidence],
            "validation": self.validation.to_dict() if self.validation else None,
            "note": self.note,
        }


@dataclass(frozen=True)
class ObservationResult:
    """Immutable result of logical observe(event): deduplicated append without governance mutation."""

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
        criteria: Mapping[str, Any] | QueryCriteria | None = None,
        as_of: str | None = None,
        *,
        allow_local_assertions: bool | None = None,
    ) -> QueryResult:
        """Read-only query returning deeply immutable records annotated with authority labels."""
        crit = QueryCriteria.from_mapping(criteria)
        eval_as_of = as_of or utc_now_iso()
        eval_dt = parse_rfc3339(eval_as_of)
        eff = self.effective_state(as_of=eval_as_of, allow_local_assertions=allow_local_assertions)

        allowed_kinds = set(crit.kinds) if crit.kinds else None
        allowed_ids = set(crit.record_ids) if crit.record_ids else None
        text_filter = crit.text.lower() if crit.text else None
        source_type_filter = crit.source_type
        scope_ctx = (
            {
                "repository": crit.repository,
                "paths": list(crit.paths) or None,
                "languages": list(crit.languages) or None,
                "conditions": list(crit.conditions) or None,
            }
            if crit.repository
            else None
        )

        matched: list[AnnotatedRecord] = []
        for rec in self.store.list_records():
            rec_id = str(rec.get("record_id", ""))
            kind = str(rec.get("kind", ""))

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
            imm = ImmutableRecord.from_mapping(rec)
            matched.append(
                AnnotatedRecord(
                    record=imm,
                    record_sha256=imm.record_sha256,
                    authority_class=auth_class,
                    lifecycle=lifecycle,
                )
            )

        return QueryResult(
            as_of=eval_as_of,
            commit_ref=self.store.snapshot_commit(),
            computation_version=COMPUTATION_VERSION,
            records=tuple(matched),
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
        candidate: Mapping[str, Any] | ImmutableRecord,
        *,
        evidence: Sequence[Mapping[str, Any] | ImmutableRecord] = (),
        write: bool = False,
    ) -> ProposalResult:
        """Validate and optionally atomically persist an inert PracticeCandidate (and Evidence).

        Never grants rights or activates any practice.
        """
        cand_dict: RecordDict = (
            candidate.to_dict() if isinstance(candidate, ImmutableRecord) else unfreeze_value(candidate)
        )
        ev_dicts: list[RecordDict] = [
            ev.to_dict() if isinstance(ev, ImmutableRecord) else unfreeze_value(ev)
            for ev in evidence
        ]

        if cand_dict.get("kind") != RecordKind.PRACTICE_CANDIDATE:
            raise ValueError("propose() only accepts 'practice_candidate' records.")
        for ev in ev_dicts:
            if ev.get("kind") != RecordKind.EVIDENCE:
                raise ValueError("propose() evidence parameter only accepts 'evidence' records.")

        combined = [*self.store.list_records(), *ev_dicts, cand_dict]
        report = validate_records(combined)
        if not report.valid:
            return ProposalResult(
                written=False,
                valid=False,
                candidate=freeze_value(cand_dict),
                evidence=tuple(freeze_value(e) for e in ev_dicts),
                validation=report,
            )

        written_ids: list[str] = []
        if write:
            # Atomic multi-record write: all evidence + candidate persist together or not at all
            written_ids = self.store.append_many([*ev_dicts, cand_dict])

        return ProposalResult(
            written=write,
            valid=True,
            candidate=freeze_value(cand_dict),
            evidence=tuple(freeze_value(e) for e in ev_dicts),
            written_record_ids=tuple(written_ids),
            validation=report,
        )

    def resolve(
        self,
        context: Mapping[str, Any] | ResolutionContext,
        as_of: str | None = None,
        *,
        allow_local_assertions: bool | None = None,
    ) -> ResolutionResult:
        """Read-only projection of verified effective practices and applicable applications."""
        ctx_map = context.to_mapping() if isinstance(context, ResolutionContext) else context
        eff = self.effective_state(as_of=as_of, allow_local_assertions=allow_local_assertions)
        return resolve_context(eff, ctx_map)

    def observe(
        self,
        event: Mapping[str, Any] | ImmutableRecord,
        *,
        batch_id: str | None = None,
    ) -> ObservationResult:
        """Append a deduplicated, immutable OutcomeEvent without altering governance state."""
        ev_dict: RecordDict = (
            event.to_dict() if isinstance(event, ImmutableRecord) else unfreeze_value(event)
        )
        if ev_dict.get("kind") != RecordKind.OUTCOME_EVENT:
            raise ValueError("observe() only accepts 'outcome_event' records.")
        existing_records = self.store.list_records()
        dedup_key = str(ev_dict.get("body", {}).get("dedup_key", ""))
        for rec in existing_records:
            if (
                rec.get("kind") == RecordKind.OUTCOME_EVENT
                and rec.get("body", {}).get("dedup_key") == dedup_key
            ):
                if canonical_sha256(rec["body"]) == canonical_sha256(ev_dict["body"]):
                    return ObservationResult(
                        appended=False,
                        deduplicated=True,
                        record_id=str(rec["record_id"]),
                        dedup_key=dedup_key,
                    )
                raise ValueError(
                    f"Conflicting outcome_event for existing dedup_key {dedup_key!r}."
                )

        report = validate_records([*existing_records, ev_dict])
        if not report.valid:
            msg = "; ".join(f"[{e.code}] {e.message}" for e in report.errors)
            raise ValueError(f"Invalid outcome_event: {msg}")

        if self.outcome_sink is not None:
            appended, rec_id = self.outcome_sink.record_outcome(ev_dict, batch_id=batch_id)
            return ObservationResult(
                appended=appended,
                deduplicated=not appended,
                record_id=rec_id,
                dedup_key=dedup_key,
            )

        rec_id = self.store.append(ev_dict)
        return ObservationResult(
            appended=True,
            deduplicated=False,
            record_id=rec_id,
            dedup_key=dedup_key,
        )
