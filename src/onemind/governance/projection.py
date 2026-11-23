"""Deterministic EffectiveView(c, t, p) projection and fail-closed context resolution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from onemind.adapters.interfaces import AuthorityVerificationResult, AuthorityVerifier
from onemind.governance.authority import DefaultAuthorityVerifier
from onemind.governance.policy import AuthorityPolicy
from onemind.protocol.canonical import canonical_sha256
from onemind.protocol.models import (
    COMPUTATION_VERSION,
    AdjudicationDisposition,
    AuthorityClass,
    ConflictRelation,
    GovernanceAction,
    RecordDict,
    RecordKind,
    ResolutionState,
    freeze_value,
    parse_rfc3339,
    unfreeze_value,
    utc_now_iso,
)
from onemind.protocol.scope import (
    evaluate_scope_applicability,
    is_scope_subset,
    scope_matches_context,
)
from onemind.protocol.validator import ValidationReport, validate_records


@dataclass(frozen=True)
class TargetState:
    """Projected lifecycle state for a single practice_version or application record."""

    record_id: str
    kind: str
    lifecycle: str  # "unapproved" | "approved" | "suspended" | "retired" | "ambiguous"
    authority_class: AuthorityClass
    verified_repository_approval: bool
    deciding_record_ids: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "kind": self.kind,
            "lifecycle": self.lifecycle,
            "authority_class": self.authority_class.value,
            "verified_repository_approval": self.verified_repository_approval,
            "deciding_record_ids": list(self.deciding_record_ids),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AdjudicationState:
    """Immutable projected resolution state of a Conflict record."""

    conflict_record_id: str
    left_record_id: str
    right_record_id: str
    relation: str
    adjudicated: bool
    disposition: str | None = None
    explanation: str | None = None
    exception_scope: Mapping[str, Any] | None = None
    excepted_record_id: str | None = None
    decision_record_id: str | None = None
    authority_class: AuthorityClass = AuthorityClass.UNAPPROVED
    ambiguous_adjudication: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_record_id": self.conflict_record_id,
            "left_record_id": self.left_record_id,
            "right_record_id": self.right_record_id,
            "relation": self.relation,
            "adjudicated": self.adjudicated,
            "disposition": self.disposition,
            "explanation": self.explanation,
            "exception_scope": unfreeze_value(self.exception_scope) if self.exception_scope else None,
            "excepted_record_id": self.excepted_record_id,
            "decision_record_id": self.decision_record_id,
            "authority_class": self.authority_class.value,
            "ambiguous_adjudication": self.ambiguous_adjudication,
        }


@dataclass(frozen=True)
class EffectiveState:
    """Deterministic projected state of all governed practices, applications, and conflicts."""

    as_of: str
    commit_ref: str | None
    policy: AuthorityPolicy
    computation_version: str
    indeterminate: bool
    indeterminate_reasons: tuple[str, ...]
    records_by_id: Mapping[str, RecordDict]
    practice_states: Mapping[str, TargetState]
    application_states: Mapping[str, TargetState]
    conflict_states: Mapping[str, AdjudicationState]
    verification_log: tuple[AuthorityVerificationResult, ...]
    validation_report: ValidationReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "commit_ref": self.commit_ref,
            "policy_version": self.policy.policy_version,
            "computation_version": self.computation_version,
            "indeterminate": self.indeterminate,
            "indeterminate_reasons": list(self.indeterminate_reasons),
            "practice_states": {k: v.to_dict() for k, v in sorted(self.practice_states.items())},
            "application_states": {
                k: v.to_dict() for k, v in sorted(self.application_states.items())
            },
            "conflict_states": {k: v.to_dict() for k, v in sorted(self.conflict_states.items())},
            "verification_log": [v.to_dict() for v in self.verification_log],
        }


@dataclass(frozen=True)
class ResolvedPractice:
    """Deeply immutable effective practice matched to a context with full provenance."""

    record_id: str
    practice_id: str
    version: int
    statement: str
    scope: Mapping[str, Any]
    owner: str
    evidence_ids: tuple[str, ...]
    authority_class: AuthorityClass
    verified_repository_approval: bool
    deciding_record_ids: tuple[str, ...]
    record_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "practice_id": self.practice_id,
            "version": self.version,
            "statement": self.statement,
            "scope": unfreeze_value(self.scope),
            "owner": self.owner,
            "evidence_ids": list(self.evidence_ids),
            "authority_class": self.authority_class.value,
            "verified_repository_approval": self.verified_repository_approval,
            "deciding_record_ids": list(self.deciding_record_ids),
            "record_sha256": self.record_sha256,
        }


@dataclass(frozen=True)
class ResolvedApplication:
    """Deeply immutable effective application matched to a context with full provenance."""

    record_id: str
    application_id: str
    application_version: int
    practice_version_id: str
    route: str
    applicability: Mapping[str, Any]
    detection: Mapping[str, Any] | None
    authority_class: AuthorityClass
    verified_repository_approval: bool
    deciding_record_ids: tuple[str, ...]
    record_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "application_id": self.application_id,
            "application_version": self.application_version,
            "practice_version_id": self.practice_version_id,
            "route": self.route,
            "applicability": unfreeze_value(self.applicability),
            "detection": unfreeze_value(self.detection) if self.detection is not None else None,
            "authority_class": self.authority_class.value,
            "verified_repository_approval": self.verified_repository_approval,
            "deciding_record_ids": list(self.deciding_record_ids),
            "record_sha256": self.record_sha256,
        }


@dataclass(frozen=True)
class ResolutionResult:
    """Deeply immutable explicit result of resolve(context, as_of)."""

    state: ResolutionState
    context: Mapping[str, Any]
    as_of: str
    commit_ref: str | None
    policy_version: str
    computation_version: str
    practices: tuple[ResolvedPractice, ...] = field(default_factory=tuple)
    applications: tuple[ResolvedApplication, ...] = field(default_factory=tuple)
    suspended_records: tuple[str, ...] = field(default_factory=tuple)
    conflicts: tuple[AdjudicationState, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "context": unfreeze_value(self.context),
            "as_of": self.as_of,
            "commit_ref": self.commit_ref,
            "policy_version": self.policy_version,
            "computation_version": self.computation_version,
            "practices": [p.to_dict() for p in self.practices],
            "applications": [a.to_dict() for a in self.applications],
            "suspended_records": list(self.suspended_records),
            "conflicts": [c.to_dict() for c in self.conflicts],
            "reasons": list(self.reasons),
        }


def _apply_lifecycle_decisions(
    target_id: str,
    target_kind: str,
    decisions: list[tuple[datetime, int | None, RecordDict, AuthorityVerificationResult]],
) -> TargetState:
    """Deterministically reduce ordered, verified governance decisions for a single target."""
    if not decisions:
        return TargetState(
            record_id=target_id,
            kind=target_kind,
            lifecycle="unapproved",
            authority_class=AuthorityClass.UNAPPROVED,
            verified_repository_approval=False,
            deciding_record_ids=(),
            reason="No qualifying effective governance decisions.",
        )

    ordered = sorted(
        decisions,
        key=lambda item: (
            item[0],
            item[1] if item[1] is not None else 10**12,
            str(item[2]["record_id"]),
        ),
    )

    # Detect ambiguous unsequenced simultaneous conflicting actions (never use created_at as tie-breaker)
    for idx in range(len(ordered) - 1):
        t1, seq1, d1, _ = ordered[idx]
        t2, seq2, d2, _ = ordered[idx + 1]
        if (
            t1 == t2
            and (seq1 is None or seq2 is None or seq1 == seq2)
            and d1["body"]["action"] != d2["body"]["action"]
        ):
            return TargetState(
                record_id=target_id,
                kind=target_kind,
                lifecycle="ambiguous",
                authority_class=AuthorityClass.INVALID_AUTHORITY,
                verified_repository_approval=False,
                deciding_record_ids=(str(d1["record_id"]), str(d2["record_id"])),
                reason=(
                    f"Ambiguous simultaneous governance actions "
                    f"({d1['body']['action']!r} vs {d2['body']['action']!r}) at {t1.isoformat()} "
                    "without ledger sequence ordering."
                ),
            )

    lifecycle = "unapproved"
    auth_class = AuthorityClass.UNAPPROVED
    verified_repo = False
    deciding_ids: list[str] = []

    for _, _, dec, ver in ordered:
        action = str(dec["body"]["action"])
        dec_id = str(dec["record_id"])

        if lifecycle == "retired":
            continue

        if action == GovernanceAction.APPROVE:
            if lifecycle in ("unapproved", "approved"):
                lifecycle = "approved"
                auth_class = ver.authority_class
                verified_repo = ver.authority_class == AuthorityClass.VERIFIED_REPOSITORY_APPROVAL
                deciding_ids.append(dec_id)
        elif action == GovernanceAction.SUSPEND:
            if lifecycle in ("approved", "suspended"):
                lifecycle = "suspended"
                deciding_ids.append(dec_id)
        elif action == GovernanceAction.RESTORE:
            if lifecycle == "suspended":
                lifecycle = "approved"
                if ver.authority_class != AuthorityClass.VERIFIED_REPOSITORY_APPROVAL:
                    auth_class = ver.authority_class
                    verified_repo = False
                deciding_ids.append(dec_id)
        elif action == GovernanceAction.RETIRE:
            lifecycle = "retired"
            deciding_ids.append(dec_id)

    return TargetState(
        record_id=target_id,
        kind=target_kind,
        lifecycle=lifecycle,
        authority_class=auth_class,
        verified_repository_approval=verified_repo,
        deciding_record_ids=tuple(deciding_ids),
        reason=f"Projected lifecycle={lifecycle!r} from {len(deciding_ids)} qualifying decision(s).",
    )


def project_effective_state(
    records: Sequence[RecordDict],
    *,
    as_of: str | None = None,
    policy: AuthorityPolicy | None = None,
    verifier: AuthorityVerifier | None = None,
    commit_ref: str | None = None,
    ledger_order: Mapping[str, int] | None = None,
) -> EffectiveState:
    """Compute EffectiveView(c, t, p) purely and deterministically from a record set."""
    eval_as_of = as_of or utc_now_iso()
    eval_dt = parse_rfc3339(eval_as_of)
    auth_policy = policy or AuthorityPolicy()
    auth_verifier = verifier or DefaultAuthorityVerifier()
    seq_map = dict(ledger_order) if ledger_order else {}

    report = validate_records(records)
    by_id: dict[str, RecordDict] = {}
    indeterminate_reasons: list[str] = []

    # Fatal structural/version/integrity errors make projection indeterminate (fail-closed).
    fatal_codes = {
        "INVALID_RECORD_TYPE",
        "MISSING_SCHEMA_VERSION",
        "UNSUPPORTED_SCHEMA_VERSION",
        "UNKNOWN_RECORD_KIND",
        "DUPLICATE_RECORD_ID",
        "DANGLING_REFERENCE",
        "INVALID_VERSION_CHAIN",
        "INVALID_APPLICATION_VERSION_CHAIN",
        "DUPLICATE_PRACTICE_VERSION",
        "DUPLICATE_APPLICATION_VERSION",
        "CANONICALIZATION_ERROR",
    }
    for err in report.errors:
        if err.code in fatal_codes:
            indeterminate_reasons.append(f"[{err.code}] {err.message}")

    for rec in records:
        if isinstance(rec, dict) and isinstance(rec.get("record_id"), str):
            rid = rec["record_id"]
            if rid not in by_id:
                by_id[rid] = rec

    if indeterminate_reasons:
        return EffectiveState(
            as_of=eval_as_of,
            commit_ref=commit_ref,
            policy=auth_policy,
            computation_version=COMPUTATION_VERSION,
            indeterminate=True,
            indeterminate_reasons=tuple(indeterminate_reasons),
            records_by_id=by_id,
            practice_states={},
            application_states={},
            conflict_states={},
            verification_log=(),
            validation_report=report,
        )

    # Verify all governance decisions whose effective_at <= as_of (ignoring backdated decisions)
    verification_log: list[AuthorityVerificationResult] = []
    decisions_by_target: dict[
        str, list[tuple[datetime, int | None, RecordDict, AuthorityVerificationResult]]
    ] = {}

    governance_records = [
        r for r in by_id.values() if r.get("kind") == RecordKind.GOVERNANCE_DECISION
    ]
    governance_records.sort(key=lambda r: str(r["record_id"]))

    for dec in governance_records:
        body = dec["body"]
        eff_dt = parse_rfc3339(str(body["effective_at"]))
        if eff_dt > eval_dt:
            continue
        target_id = str(body["target_record_id"])
        target = by_id.get(target_id)
        # Fail closed on backdated governance decisions
        if target is not None and eff_dt < parse_rfc3339(str(target["created_at"])):
            verification_log.append(
                AuthorityVerificationResult(
                    decision_record_id=str(dec["record_id"]),
                    valid_proof=False,
                    authority_class=AuthorityClass.INVALID_AUTHORITY,
                    effective_for_policy=False,
                    reason="Governance decision effective_at predates target record created_at.",
                )
            )
            continue
        ver = auth_verifier.verify(dec, target, auth_policy)
        verification_log.append(ver)
        if not ver.effective_for_policy:
            continue
        seq = seq_map.get(str(dec["record_id"]))
        decisions_by_target.setdefault(target_id, []).append((eff_dt, seq, dec, ver))

    # Project PracticeVersion states
    practice_states: dict[str, TargetState] = {}
    for rec_id, rec in sorted(by_id.items()):
        if rec["kind"] != RecordKind.PRACTICE_VERSION:
            continue
        practice_states[rec_id] = _apply_lifecycle_decisions(
            rec_id,
            RecordKind.PRACTICE_VERSION.value,
            decisions_by_target.get(rec_id, []),
        )

    # Project Application states (coupled to governing PracticeVersion + scope containment)
    application_states: dict[str, TargetState] = {}
    for rec_id, rec in sorted(by_id.items()):
        if rec["kind"] != RecordKind.APPLICATION:
            continue
        app_body = rec["body"]
        pv_id = str(app_body["practice_version_id"])
        pv = by_id.get(pv_id)
        pv_state = practice_states.get(pv_id)

        if pv is None or pv_state is None:
            application_states[rec_id] = TargetState(
                record_id=rec_id,
                kind=RecordKind.APPLICATION.value,
                lifecycle="unapproved",
                authority_class=AuthorityClass.INVALID_AUTHORITY,
                verified_repository_approval=False,
                deciding_record_ids=(),
                reason=f"Governing practice_version {pv_id!r} missing.",
            )
            continue

        scope_ok, scope_reason = is_scope_subset(app_body["applicability"], pv["body"]["scope"])
        if not scope_ok:
            application_states[rec_id] = TargetState(
                record_id=rec_id,
                kind=RecordKind.APPLICATION.value,
                lifecycle="unapproved",
                authority_class=AuthorityClass.INVALID_AUTHORITY,
                verified_repository_approval=False,
                deciding_record_ids=(),
                reason=f"Application blocked by scope overreach or ambiguity: {scope_reason}",
            )
            continue

        raw_app_state = _apply_lifecycle_decisions(
            rec_id,
            RecordKind.APPLICATION.value,
            decisions_by_target.get(rec_id, []),
        )

        if pv_state.lifecycle == "retired":
            application_states[rec_id] = TargetState(
                record_id=rec_id,
                kind=RecordKind.APPLICATION.value,
                lifecycle="retired",
                authority_class=raw_app_state.authority_class,
                verified_repository_approval=False,
                deciding_record_ids=raw_app_state.deciding_record_ids + pv_state.deciding_record_ids,
                reason=f"Invalidated because governing practice_version {pv_id!r} is retired.",
            )
        elif pv_state.lifecycle == "suspended":
            application_states[rec_id] = TargetState(
                record_id=rec_id,
                kind=RecordKind.APPLICATION.value,
                lifecycle="suspended",
                authority_class=raw_app_state.authority_class,
                verified_repository_approval=False,
                deciding_record_ids=raw_app_state.deciding_record_ids + pv_state.deciding_record_ids,
                reason=f"Suspended because governing practice_version {pv_id!r} is suspended.",
            )
        elif pv_state.lifecycle != "approved":
            application_states[rec_id] = TargetState(
                record_id=rec_id,
                kind=RecordKind.APPLICATION.value,
                lifecycle="unapproved",
                authority_class=AuthorityClass.UNAPPROVED,
                verified_repository_approval=False,
                deciding_record_ids=raw_app_state.deciding_record_ids,
                reason=f"Inactive because governing practice_version {pv_id!r} is {pv_state.lifecycle!r}.",
            )
        else:
            both_verified = (
                pv_state.verified_repository_approval
                and raw_app_state.verified_repository_approval
            )
            eff_auth = (
                AuthorityClass.VERIFIED_REPOSITORY_APPROVAL
                if both_verified
                else raw_app_state.authority_class
            )
            application_states[rec_id] = TargetState(
                record_id=rec_id,
                kind=RecordKind.APPLICATION.value,
                lifecycle=raw_app_state.lifecycle,
                authority_class=eff_auth,
                verified_repository_approval=both_verified,
                deciding_record_ids=raw_app_state.deciding_record_ids,
                reason=raw_app_state.reason,
            )

    # Project Conflict adjudication states (fail closed on simultaneous conflicting adjudications)
    conflict_states: dict[str, AdjudicationState] = {}
    for rec_id, rec in sorted(by_id.items()):
        if rec["kind"] != RecordKind.CONFLICT:
            continue
        cbody = rec["body"]
        adj_decisions = [
            item
            for item in decisions_by_target.get(rec_id, [])
            if item[2]["body"]["action"] == GovernanceAction.ADJUDICATE
        ]
        if not adj_decisions:
            conflict_states[rec_id] = AdjudicationState(
                conflict_record_id=rec_id,
                left_record_id=str(cbody["left_record_id"]),
                right_record_id=str(cbody["right_record_id"]),
                relation=str(cbody["relation"]),
                adjudicated=False,
            )
            continue

        adj_decisions.sort(
            key=lambda item: (
                item[0],
                item[1] if item[1] is not None else 10**12,
                str(item[2]["record_id"]),
            )
        )
        # Check for ambiguous simultaneous conflicting dispositions
        latest_t, latest_seq, latest_dec, latest_ver = adj_decisions[-1]
        ambiguous_adj = False
        for t_prev, seq_prev, dec_prev, _ in adj_decisions[:-1]:
            if (
                t_prev == latest_t
                and (seq_prev is None or latest_seq is None or seq_prev == latest_seq)
                and dec_prev["body"].get("resolution", {}).get("disposition")
                != latest_dec["body"].get("resolution", {}).get("disposition")
            ):
                ambiguous_adj = True
                break

        if ambiguous_adj:
            conflict_states[rec_id] = AdjudicationState(
                conflict_record_id=rec_id,
                left_record_id=str(cbody["left_record_id"]),
                right_record_id=str(cbody["right_record_id"]),
                relation=str(cbody["relation"]),
                adjudicated=False,
                ambiguous_adjudication=True,
            )
        else:
            res = latest_dec["body"].get("resolution", {})
            exc_scope = res.get("exception_scope")
            conflict_states[rec_id] = AdjudicationState(
                conflict_record_id=rec_id,
                left_record_id=str(cbody["left_record_id"]),
                right_record_id=str(cbody["right_record_id"]),
                relation=str(cbody["relation"]),
                adjudicated=True,
                disposition=res.get("disposition"),
                explanation=res.get("explanation"),
                exception_scope=freeze_value(exc_scope) if isinstance(exc_scope, Mapping) else None,
                excepted_record_id=res.get("excepted_record_id"),
                decision_record_id=str(latest_dec["record_id"]),
                authority_class=latest_ver.authority_class,
            )

    return EffectiveState(
        as_of=eval_as_of,
        commit_ref=commit_ref,
        policy=auth_policy,
        computation_version=COMPUTATION_VERSION,
        indeterminate=False,
        indeterminate_reasons=(),
        records_by_id=by_id,
        practice_states=practice_states,
        application_states=application_states,
        conflict_states=conflict_states,
        verification_log=tuple(verification_log),
        validation_report=report,
    )


def resolve_context(
    effective_state: EffectiveState,
    context: Mapping[str, Any],
) -> ResolutionResult:
    """Resolve effective practices and applications for a specific context.

    Returns deeply immutable ResolutionResult with explicit state:
    none, active, ambiguous, suspended, or indeterminate.
    Fails closed whenever scope applicability or governance ordering is ambiguous.
    """
    frozen_ctx: Mapping[str, Any] = freeze_value(context) if isinstance(context, Mapping) else freeze_value({})

    if effective_state.indeterminate:
        return ResolutionResult(
            state=ResolutionState.INDETERMINATE,
            context=frozen_ctx,
            as_of=effective_state.as_of,
            commit_ref=effective_state.commit_ref,
            policy_version=effective_state.policy.policy_version,
            computation_version=effective_state.computation_version,
            reasons=effective_state.indeterminate_reasons,
        )

    if not isinstance(context, Mapping) or not isinstance(context.get("repository"), str) or not str(context.get("repository")).strip():
        return ResolutionResult(
            state=ResolutionState.INDETERMINATE,
            context=frozen_ctx,
            as_of=effective_state.as_of,
            commit_ref=effective_state.commit_ref,
            policy_version=effective_state.policy.policy_version,
            computation_version=effective_state.computation_version,
            reasons=("Resolution context must specify a non-empty string 'repository'.",),
        )

    matched_active_ids: list[str] = []
    suspended_ids: list[str] = []
    ambiguous_reasons: list[str] = []

    for pv_id, st in sorted(effective_state.practice_states.items()):
        pv = effective_state.records_by_id[pv_id]
        app_status, app_reason = evaluate_scope_applicability(pv["body"]["scope"], context)
        if app_status == "ambiguous":
            if st.lifecycle in ("approved", "ambiguous"):
                ambiguous_reasons.append(
                    f"Ambiguous applicability for practice {pv_id!r}: {app_reason}"
                )
            continue
        if app_status != "match":
            continue
        if st.lifecycle == "ambiguous":
            ambiguous_reasons.append(f"Practice {pv_id!r} has ambiguous governance state: {st.reason}")
        elif st.lifecycle == "suspended":
            suspended_ids.append(pv_id)
        elif st.lifecycle == "approved":
            matched_active_ids.append(pv_id)

    # Evaluate conflicts among currently matched active practice versions.
    # Candidate conflicts NEVER affect approved practices (non-interference invariant).
    suppressed_by_adjudication: set[str] = set()
    unresolved_conflicts: list[AdjudicationState] = []
    relevant_conflicts: list[AdjudicationState] = []
    active_set = set(matched_active_ids)

    for c_state in effective_state.conflict_states.values():
        if c_state.left_record_id in active_set and c_state.right_record_id in active_set:
            relevant_conflicts.append(c_state)
            if c_state.ambiguous_adjudication:
                unresolved_conflicts.append(c_state)
                ambiguous_reasons.append(
                    f"Conflict {c_state.conflict_record_id!r} has ambiguous simultaneous adjudications."
                )
            elif c_state.adjudicated:
                disp = c_state.disposition
                if disp == AdjudicationDisposition.PREFER_LEFT:
                    suppressed_by_adjudication.add(c_state.right_record_id)
                elif disp == AdjudicationDisposition.PREFER_RIGHT:
                    suppressed_by_adjudication.add(c_state.left_record_id)
                elif disp == AdjudicationDisposition.SUSPEND_BOTH:
                    suppressed_by_adjudication.add(c_state.left_record_id)
                    suppressed_by_adjudication.add(c_state.right_record_id)
                    suspended_ids.extend([c_state.left_record_id, c_state.right_record_id])
                elif disp == AdjudicationDisposition.SCOPED_EXCEPTION:
                    if not c_state.exception_scope or not c_state.excepted_record_id:
                        ambiguous_reasons.append(
                            f"Scoped exception adjudication {c_state.decision_record_id!r} "
                            "missing exception_scope or excepted_record_id."
                        )
                    else:
                        exc_status, exc_reason = evaluate_scope_applicability(
                            c_state.exception_scope, context
                        )
                        if exc_status == "ambiguous":
                            ambiguous_reasons.append(
                                f"Ambiguous scoped-exception applicability on {c_state.conflict_record_id!r}: {exc_reason}"
                            )
                        elif exc_status == "match":
                            suppressed_by_adjudication.add(c_state.excepted_record_id)
            elif c_state.relation == ConflictRelation.CONTRADICTS:
                unresolved_conflicts.append(c_state)
                ambiguous_reasons.append(
                    f"Unresolved contradictory conflict {c_state.conflict_record_id!r} between "
                    f"approved practices {c_state.left_record_id!r} and {c_state.right_record_id!r}."
                )

    effective_practice_ids = [
        pid for pid in matched_active_ids if pid not in suppressed_by_adjudication
    ]

    # Check if two distinct versions of the same practice_id are simultaneously active
    versions_by_practice_id: dict[str, list[str]] = {}
    for pid in effective_practice_ids:
        prac_id = str(effective_state.records_by_id[pid]["body"]["practice_id"])
        versions_by_practice_id.setdefault(prac_id, []).append(pid)
    for prac_id, pids in sorted(versions_by_practice_id.items()):
        if len(pids) > 1:
            ambiguous_reasons.append(
                f"Multiple simultaneously active versions for practice {prac_id!r}: {pids}."
            )

    resolved_practices: list[ResolvedPractice] = []
    for pid in effective_practice_ids:
        pv = effective_state.records_by_id[pid]
        st = effective_state.practice_states[pid]
        body = pv["body"]
        resolved_practices.append(
            ResolvedPractice(
                record_id=pid,
                practice_id=str(body["practice_id"]),
                version=int(body["version"]),
                statement=str(body["statement"]),
                scope=freeze_value(body["scope"]),
                owner=str(body["owner"]),
                evidence_ids=tuple(str(e) for e in body.get("evidence_ids", [])),
                authority_class=st.authority_class,
                verified_repository_approval=st.verified_repository_approval,
                deciding_record_ids=st.deciding_record_ids,
                record_sha256=canonical_sha256(pv),
            )
        )

    effective_practice_set = set(effective_practice_ids)
    resolved_apps: list[ResolvedApplication] = []
    for app_id, app_st in sorted(effective_state.application_states.items()):
        app = effective_state.records_by_id[app_id]
        app_body = app["body"]
        if str(app_body["practice_version_id"]) not in effective_practice_set:
            if (
                app_st.lifecycle == "suspended"
                and scope_matches_context(app_body["applicability"], context)
                and app_id not in suspended_ids
            ):
                suspended_ids.append(app_id)
            continue
        app_status, app_reason = evaluate_scope_applicability(app_body["applicability"], context)
        if app_status == "ambiguous":
            if app_st.lifecycle in ("approved", "ambiguous"):
                ambiguous_reasons.append(
                    f"Ambiguous applicability for application {app_id!r}: {app_reason}"
                )
            continue
        if app_status != "match":
            continue
        if app_st.lifecycle == "suspended":
            suspended_ids.append(app_id)
            continue
        if app_st.lifecycle == "ambiguous":
            ambiguous_reasons.append(f"Application {app_id!r} has ambiguous governance state.")
            continue
        if app_st.lifecycle != "approved":
            continue
        det = app_body.get("detection")
        resolved_apps.append(
            ResolvedApplication(
                record_id=app_id,
                application_id=str(app_body["application_id"]),
                application_version=int(app_body["application_version"]),
                practice_version_id=str(app_body["practice_version_id"]),
                route=str(app_body["route"]),
                applicability=freeze_value(app_body["applicability"]),
                detection=freeze_value(det) if isinstance(det, Mapping) else None,
                authority_class=app_st.authority_class,
                verified_repository_approval=app_st.verified_repository_approval,
                deciding_record_ids=app_st.deciding_record_ids,
                record_sha256=canonical_sha256(app),
            )
        )

    if ambiguous_reasons:
        return ResolutionResult(
            state=ResolutionState.AMBIGUOUS,
            context=frozen_ctx,
            as_of=effective_state.as_of,
            commit_ref=effective_state.commit_ref,
            policy_version=effective_state.policy.policy_version,
            computation_version=effective_state.computation_version,
            practices=tuple(resolved_practices),
            applications=tuple(resolved_apps),
            suspended_records=tuple(sorted(set(suspended_ids))),
            conflicts=tuple(unresolved_conflicts or relevant_conflicts),
            reasons=tuple(ambiguous_reasons),
        )

    if resolved_practices:
        return ResolutionResult(
            state=ResolutionState.ACTIVE,
            context=frozen_ctx,
            as_of=effective_state.as_of,
            commit_ref=effective_state.commit_ref,
            policy_version=effective_state.policy.policy_version,
            computation_version=effective_state.computation_version,
            practices=tuple(resolved_practices),
            applications=tuple(resolved_apps),
            suspended_records=tuple(sorted(set(suspended_ids))),
            conflicts=tuple(relevant_conflicts),
            reasons=(
                f"Resolved {len(resolved_practices)} active practice(s) and "
                f"{len(resolved_apps)} active application(s).",
            ),
        )

    if suspended_ids:
        return ResolutionResult(
            state=ResolutionState.SUSPENDED,
            context=frozen_ctx,
            as_of=effective_state.as_of,
            commit_ref=effective_state.commit_ref,
            policy_version=effective_state.policy.policy_version,
            computation_version=effective_state.computation_version,
            suspended_records=tuple(sorted(set(suspended_ids))),
            conflicts=tuple(relevant_conflicts),
            reasons=(f"Matching record(s) currently suspended: {sorted(set(suspended_ids))}.",),
        )

    return ResolutionResult(
        state=ResolutionState.NONE,
        context=frozen_ctx,
        as_of=effective_state.as_of,
        commit_ref=effective_state.commit_ref,
        policy_version=effective_state.policy.policy_version,
        computation_version=effective_state.computation_version,
        reasons=("No approved practices apply to the given context.",),
    )
