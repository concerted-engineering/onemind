"""Semantic cross-record validation and governance invariant enforcement."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from onemind.protocol.canonical import canonical_sha256, excerpt_sha256
from onemind.protocol.models import (
    ActorType,
    AdjudicationDisposition,
    GovernanceAction,
    RecordDict,
    RecordKind,
    is_safe_record_id,
)
from onemind.protocol.schema import validate_record_schema
from onemind.protocol.scope import is_scope_subset

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class ValidationIssue:
    """A structured validation error or warning."""

    code: str
    severity: Severity
    message: str
    record_id: str | None = None
    field_path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.record_id is not None:
            out["record_id"] = self.record_id
        if self.field_path is not None:
            out["field_path"] = self.field_path
        if self.details:
            out["details"] = self.details
        return out


@dataclass
class ValidationReport:
    """Aggregate result of structural, semantic, and optional historical validation."""

    valid: bool
    issues: list[ValidationIssue]
    record_count: int
    counts_by_kind: dict[str, int]
    historical_verification: dict[str, Any] = field(
        default_factory=lambda: {
            "status": "unavailable",
            "reason": "No trusted Git base was supplied for historical mutation verification.",
        }
    )

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "record_count": self.record_count,
            "counts_by_kind": self.counts_by_kind,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "historical_verification": self.historical_verification,
            "issues": [i.to_dict() for i in self.issues],
        }


def validate_records(
    records: Sequence[RecordDict],
    *,
    strict_digests: bool = True,
) -> ValidationReport:
    """Run structural JSON Schema validation and semantic cross-record validation."""
    issues: list[ValidationIssue] = []
    counts: Counter[str] = Counter()
    by_id: dict[str, RecordDict] = {}
    digests_by_id: dict[str, str] = {}
    valid_records: list[RecordDict] = []

    # Pass 1: Structural schema, safe record_id, duplicate IDs, canonical digests
    for idx, rec in enumerate(records):
        schema_errors = validate_record_schema(rec)
        if schema_errors:
            for se in schema_errors:
                issues.append(
                    ValidationIssue(
                        code=se.code,
                        severity="error",
                        message=se.message,
                        record_id=se.record_id or f"<index:{idx}>",
                        field_path=se.path,
                        details=se.details,
                    )
                )
            continue

        rec_id = str(rec["record_id"])
        kind = str(rec["kind"])
        counts[kind] += 1

        if not is_safe_record_id(rec_id):
            issues.append(
                ValidationIssue(
                    code="UNSAFE_RECORD_ID",
                    severity="error",
                    message=(
                        f"record_id {rec_id!r} contains unsafe characters or path traversal segments."
                    ),
                    record_id=rec_id,
                    field_path="record_id",
                )
            )
            continue

        if rec_id in by_id:
            issues.append(
                ValidationIssue(
                    code="DUPLICATE_RECORD_ID",
                    severity="error",
                    message=f"Duplicate record_id {rec_id!r} detected; record IDs must never be reused.",
                    record_id=rec_id,
                    field_path="record_id",
                )
            )
            continue

        try:
            digest = canonical_sha256(rec)
        except (TypeError, ValueError) as exc:
            issues.append(
                ValidationIssue(
                    code="CANONICALIZATION_ERROR",
                    severity="error",
                    message=f"Record could not be canonicalized per RFC 8785: {exc}",
                    record_id=rec_id,
                )
            )
            continue

        by_id[rec_id] = rec
        digests_by_id[rec_id] = digest
        valid_records.append(rec)

    # Track uniqueness constraints across versions and dedup keys
    seen_practice_versions: dict[tuple[str, int], str] = {}
    seen_app_versions: dict[tuple[str, int], str] = {}
    seen_dedup_keys: dict[str, tuple[str, str]] = {}
    # Track experiment assignments: (experiment_id, repo_id, pr_id) -> (arm, record_id)
    assignments_by_pr: dict[tuple[str, str, str], tuple[str | None, str]] = {}

    # Pre-index outcome assignment events
    for rec in valid_records:
        if rec["kind"] != RecordKind.OUTCOME_EVENT:
            continue
        ev_body: dict[str, Any] = rec["body"]
        exp_id = ev_body.get("experiment_id")
        if exp_id and ev_body.get("event_type") == "assignment":
            key = (str(exp_id), str(ev_body["repo_id"]), str(ev_body["pr_id"]))
            arm = ev_body.get("details", {}).get("arm")
            if key in assignments_by_pr:
                prev_arm, prev_id = assignments_by_pr[key]
                if prev_arm != arm:
                    issues.append(
                        ValidationIssue(
                            code="EXPERIMENT_ASSIGNMENT_CHANGED",
                            severity="error",
                            message=(
                                f"Experiment assignment for {key} changed from {prev_arm!r} "
                                f"({prev_id}) to {arm!r} ({rec['record_id']}); assignment must "
                                "remain immutable across PR revisions."
                            ),
                            record_id=rec["record_id"],
                            field_path="body.details.arm",
                        )
                    )
            else:
                assignments_by_pr[key] = (arm, rec["record_id"])

    # Pass 2: Semantic cross-record validation
    for rec in valid_records:
        rec_id = str(rec["record_id"])
        kind = str(rec["kind"])
        body: dict[str, Any] = rec["body"]

        if kind == RecordKind.EVIDENCE:
            _validate_evidence(rec_id, body, issues, strict_digests=strict_digests)

        elif kind == RecordKind.PRACTICE_CANDIDATE:
            _validate_practice_candidate(rec_id, body, by_id, issues)

        elif kind == RecordKind.PRACTICE_VERSION:
            _validate_practice_version(
                rec_id, body, by_id, seen_practice_versions, issues
            )

        elif kind == RecordKind.APPLICATION:
            _validate_application(rec_id, body, by_id, seen_app_versions, issues)

        elif kind == RecordKind.CONFLICT:
            _validate_conflict(rec_id, body, by_id, issues)

        elif kind == RecordKind.GOVERNANCE_DECISION:
            _validate_governance_decision(
                rec, by_id, digests_by_id, issues
            )

        elif kind == RecordKind.OUTCOME_EVENT:
            _validate_outcome_event(
                rec_id,
                body,
                digests_by_id[rec_id],
                by_id,
                seen_dedup_keys,
                assignments_by_pr,
                issues,
            )

    has_errors = any(i.severity == "error" for i in issues)
    return ValidationReport(
        valid=not has_errors,
        issues=issues,
        record_count=len(records),
        counts_by_kind=dict(counts),
    )


def _validate_evidence(
    rec_id: str,
    body: dict[str, Any],
    issues: list[ValidationIssue],
    *,
    strict_digests: bool,
) -> None:
    source_state = body.get("source_state")
    snapshot = body.get("redacted_snapshot")
    redaction = body.get("redaction")

    if source_state == "source-unavailable" and snapshot:
        issues.append(
            ValidationIssue(
                code="UNAVAILABLE_SOURCE_HAS_SNAPSHOT",
                severity="error",
                message="Evidence marked 'source-unavailable' must not include a non-empty redacted_snapshot.",
                record_id=rec_id,
                field_path="body.redacted_snapshot",
            )
        )
    elif source_state == "available" and snapshot is None:
        issues.append(
            ValidationIssue(
                code="SNAPSHOT_OMITTED",
                severity="warning",
                message=(
                    "Evidence omits 'redacted_snapshot'; excerpt text is not reproducible "
                    "from the record alone."
                ),
                record_id=rec_id,
                field_path="body.redacted_snapshot",
            )
        )
    elif isinstance(snapshot, str) and strict_digests and not redaction:
        actual = excerpt_sha256(snapshot)
        declared = body.get("excerpt_sha256")
        if actual != declared:
            issues.append(
                ValidationIssue(
                    code="EXCERPT_DIGEST_MISMATCH",
                    severity="error",
                    message=(
                        f"Evidence excerpt_sha256 {declared!r} does not match "
                        f"redacted_snapshot digest {actual!r} (no redaction metadata declared)."
                    ),
                    record_id=rec_id,
                    field_path="body.excerpt_sha256",
                    details={"declared": declared, "actual": actual},
                )
            )


def _validate_practice_candidate(
    rec_id: str,
    body: dict[str, Any],
    by_id: dict[str, RecordDict],
    issues: list[ValidationIssue],
) -> None:
    for ev_id in body.get("evidence_ids", []):
        target = by_id.get(ev_id)
        if target is None:
            issues.append(
                ValidationIssue(
                    code="DANGLING_REFERENCE",
                    severity="error",
                    message=f"PracticeCandidate references missing evidence record {ev_id!r}.",
                    record_id=rec_id,
                    field_path="body.evidence_ids",
                )
            )
        elif target["kind"] != RecordKind.EVIDENCE:
            issues.append(
                ValidationIssue(
                    code="WRONG_REFERENCE_KIND",
                    severity="error",
                    message=(
                        f"PracticeCandidate evidence_ids entry {ev_id!r} points to kind "
                        f"{target['kind']!r}, expected 'evidence'."
                    ),
                    record_id=rec_id,
                    field_path="body.evidence_ids",
                )
            )


def _validate_practice_version(
    rec_id: str,
    body: dict[str, Any],
    by_id: dict[str, RecordDict],
    seen_versions: dict[tuple[str, int], str],
    issues: list[ValidationIssue],
) -> None:
    practice_id = str(body["practice_id"])
    version = int(body["version"])
    ver_key = (practice_id, version)

    if ver_key in seen_versions:
        issues.append(
            ValidationIssue(
                code="DUPLICATE_PRACTICE_VERSION",
                severity="error",
                message=(
                    f"Duplicate practice version ({practice_id!r}, v{version}); "
                    f"already defined by {seen_versions[ver_key]!r}."
                ),
                record_id=rec_id,
                field_path="body.version",
            )
        )
    else:
        seen_versions[ver_key] = rec_id

    for ev_id in body.get("evidence_ids", []):
        target = by_id.get(ev_id)
        if target is None:
            issues.append(
                ValidationIssue(
                    code="DANGLING_REFERENCE",
                    severity="error",
                    message=f"PracticeVersion references missing evidence record {ev_id!r}.",
                    record_id=rec_id,
                    field_path="body.evidence_ids",
                )
            )
        elif target["kind"] != RecordKind.EVIDENCE:
            issues.append(
                ValidationIssue(
                    code="WRONG_REFERENCE_KIND",
                    severity="error",
                    message=(
                        f"PracticeVersion evidence_ids entry {ev_id!r} points to kind "
                        f"{target['kind']!r}, expected 'evidence'."
                    ),
                    record_id=rec_id,
                    field_path="body.evidence_ids",
                )
            )

    cand_id = body.get("candidate_id")
    if cand_id is not None:
        cand = by_id.get(cand_id)
        if cand is None:
            issues.append(
                ValidationIssue(
                    code="DANGLING_REFERENCE",
                    severity="error",
                    message=f"PracticeVersion references missing candidate_id {cand_id!r}.",
                    record_id=rec_id,
                    field_path="body.candidate_id",
                )
            )
        elif cand["kind"] != RecordKind.PRACTICE_CANDIDATE:
            issues.append(
                ValidationIssue(
                    code="WRONG_REFERENCE_KIND",
                    severity="error",
                    message=(
                        f"PracticeVersion candidate_id {cand_id!r} points to kind "
                        f"{cand['kind']!r}, expected 'practice_candidate'."
                    ),
                    record_id=rec_id,
                    field_path="body.candidate_id",
                )
            )

    prev_id = body.get("previous_version_id")
    if version == 1 and prev_id is not None:
        issues.append(
            ValidationIssue(
                code="INVALID_VERSION_CHAIN",
                severity="error",
                message="PracticeVersion with version=1 must have previous_version_id=null.",
                record_id=rec_id,
                field_path="body.previous_version_id",
            )
        )
    elif version > 1:
        if prev_id is None:
            issues.append(
                ValidationIssue(
                    code="INVALID_VERSION_CHAIN",
                    severity="error",
                    message=f"PracticeVersion with version={version} must reference previous_version_id.",
                    record_id=rec_id,
                    field_path="body.previous_version_id",
                )
            )
        elif prev_id == rec_id:
            issues.append(
                ValidationIssue(
                    code="INVALID_VERSION_CHAIN",
                    severity="error",
                    message="PracticeVersion cannot reference itself as previous_version_id.",
                    record_id=rec_id,
                    field_path="body.previous_version_id",
                )
            )
        else:
            prev = by_id.get(prev_id)
            if prev is None:
                issues.append(
                    ValidationIssue(
                        code="DANGLING_REFERENCE",
                        severity="error",
                        message=f"PracticeVersion references missing previous_version_id {prev_id!r}.",
                        record_id=rec_id,
                        field_path="body.previous_version_id",
                    )
                )
            elif prev["kind"] != RecordKind.PRACTICE_VERSION:
                issues.append(
                    ValidationIssue(
                        code="WRONG_REFERENCE_KIND",
                        severity="error",
                        message=(
                            f"previous_version_id {prev_id!r} points to kind {prev['kind']!r}, "
                            "expected 'practice_version'."
                        ),
                        record_id=rec_id,
                        field_path="body.previous_version_id",
                    )
                )
            else:
                prev_body = prev["body"]
                if prev_body.get("practice_id") != practice_id:
                    issues.append(
                        ValidationIssue(
                            code="INVALID_VERSION_CHAIN",
                            severity="error",
                            message=(
                                f"previous_version_id {prev_id!r} belongs to practice_id "
                                f"{prev_body.get('practice_id')!r}, expected {practice_id!r}."
                            ),
                            record_id=rec_id,
                            field_path="body.previous_version_id",
                        )
                    )
                if prev_body.get("version") != version - 1:
                    issues.append(
                        ValidationIssue(
                            code="INVALID_VERSION_CHAIN",
                            severity="error",
                            message=(
                                f"Non-monotonic version chain: version={version} references "
                                f"{prev_id!r} with version={prev_body.get('version')}."
                            ),
                            record_id=rec_id,
                            field_path="body.previous_version_id",
                        )
                    )


def _validate_application(
    rec_id: str,
    body: dict[str, Any],
    by_id: dict[str, RecordDict],
    seen_app_versions: dict[tuple[str, int], str],
    issues: list[ValidationIssue],
) -> None:
    app_id = str(body["application_id"])
    app_ver = int(body["application_version"])
    ver_key = (app_id, app_ver)

    if ver_key in seen_app_versions:
        issues.append(
            ValidationIssue(
                code="DUPLICATE_APPLICATION_VERSION",
                severity="error",
                message=(
                    f"Duplicate application version ({app_id!r}, v{app_ver}); "
                    f"already defined by {seen_app_versions[ver_key]!r}."
                ),
                record_id=rec_id,
                field_path="body.application_version",
            )
        )
    else:
        seen_app_versions[ver_key] = rec_id

    pv_id = str(body["practice_version_id"])
    pv = by_id.get(pv_id)
    if pv is None:
        issues.append(
            ValidationIssue(
                code="DANGLING_REFERENCE",
                severity="error",
                message=f"Application references missing practice_version_id {pv_id!r}.",
                record_id=rec_id,
                field_path="body.practice_version_id",
            )
        )
        return
    if pv["kind"] != RecordKind.PRACTICE_VERSION:
        issues.append(
            ValidationIssue(
                code="WRONG_REFERENCE_KIND",
                severity="error",
                message=(
                    f"Application practice_version_id {pv_id!r} points to kind {pv['kind']!r}, "
                    "expected 'practice_version'."
                ),
                record_id=rec_id,
                field_path="body.practice_version_id",
            )
        )
        return

    # Critical governance invariant: Application scope cannot exceed PracticeVersion scope.
    ok, reason = is_scope_subset(body["applicability"], pv["body"]["scope"])
    if not ok:
        issues.append(
            ValidationIssue(
                code="APPLICATION_SCOPE_OVERREACH",
                severity="error",
                message=f"Application applicability exceeds governing practice scope: {reason}",
                record_id=rec_id,
                field_path="body.applicability",
                details={"practice_version_id": pv_id},
            )
        )


def _validate_conflict(
    rec_id: str,
    body: dict[str, Any],
    by_id: dict[str, RecordDict],
    issues: list[ValidationIssue],
) -> None:
    left_id = str(body["left_record_id"])
    right_id = str(body["right_record_id"])
    if left_id == right_id:
        issues.append(
            ValidationIssue(
                code="SELF_CONFLICT",
                severity="error",
                message=f"Conflict cannot compare record {left_id!r} against itself.",
                record_id=rec_id,
                field_path="body.right_record_id",
            )
        )

    valid_conflict_kinds = {RecordKind.PRACTICE_CANDIDATE, RecordKind.PRACTICE_VERSION}
    for fld, target_id in (("body.left_record_id", left_id), ("body.right_record_id", right_id)):
        target = by_id.get(target_id)
        if target is None:
            issues.append(
                ValidationIssue(
                    code="DANGLING_REFERENCE",
                    severity="error",
                    message=f"Conflict references missing record {target_id!r}.",
                    record_id=rec_id,
                    field_path=fld,
                )
            )
        elif target["kind"] not in valid_conflict_kinds:
            issues.append(
                ValidationIssue(
                    code="WRONG_REFERENCE_KIND",
                    severity="error",
                    message=(
                        f"Conflict {fld} ({target_id!r}) has kind {target['kind']!r}; "
                        "expected 'practice_candidate' or 'practice_version'."
                    ),
                    record_id=rec_id,
                    field_path=fld,
                )
            )

    for ev_id in body.get("evidence_ids", []):
        ev = by_id.get(ev_id)
        if ev is None:
            issues.append(
                ValidationIssue(
                    code="DANGLING_REFERENCE",
                    severity="error",
                    message=f"Conflict references missing evidence record {ev_id!r}.",
                    record_id=rec_id,
                    field_path="body.evidence_ids",
                )
            )
        elif ev["kind"] != RecordKind.EVIDENCE:
            issues.append(
                ValidationIssue(
                    code="WRONG_REFERENCE_KIND",
                    severity="error",
                    message=(
                        f"Conflict evidence_ids entry {ev_id!r} points to kind {ev['kind']!r}, "
                        "expected 'evidence'."
                    ),
                    record_id=rec_id,
                    field_path="body.evidence_ids",
                )
            )


def _validate_governance_decision(
    rec: RecordDict,
    by_id: dict[str, RecordDict],
    digests_by_id: dict[str, str],
    issues: list[ValidationIssue],
) -> None:
    rec_id = str(rec["record_id"])
    body: dict[str, Any] = rec["body"]
    actor_type = rec["actor"]["type"]

    # Critical governance invariant: an agent/model/extractor must never originate governance decisions.
    if actor_type == ActorType.AGENT:
        issues.append(
            ValidationIssue(
                code="UNAUTHORIZED_DECISION_ACTOR",
                severity="error",
                message="An agent actor must never originate a governance_decision.",
                record_id=rec_id,
                field_path="actor.type",
            )
        )

    target_id = str(body["target_record_id"])
    target_kind = str(body["target_kind"])
    action = str(body["action"])
    declared_sha = str(body["target_sha256"])

    target = by_id.get(target_id)
    if target is None:
        issues.append(
            ValidationIssue(
                code="DANGLING_REFERENCE",
                severity="error",
                message=f"GovernanceDecision references missing target_record_id {target_id!r}.",
                record_id=rec_id,
                field_path="body.target_record_id",
            )
        )
        return

    if target["kind"] != target_kind:
        issues.append(
            ValidationIssue(
                code="TARGET_KIND_MISMATCH",
                severity="error",
                message=(
                    f"GovernanceDecision declares target_kind={target_kind!r}, "
                    f"but {target_id!r} has kind={target['kind']!r}."
                ),
                record_id=rec_id,
                field_path="body.target_kind",
            )
        )

    # Critical governance invariant: approvals and decisions bind to exact canonical SHA-256 digest.
    actual_sha = digests_by_id.get(target_id)
    if actual_sha != declared_sha:
        issues.append(
            ValidationIssue(
                code="TARGET_DIGEST_MISMATCH",
                severity="error",
                message=(
                    f"GovernanceDecision target_sha256 {declared_sha!r} does not match "
                    f"canonical RFC 8785 digest {actual_sha!r} of {target_id!r}."
                ),
                record_id=rec_id,
                field_path="body.target_sha256",
                details={"declared": declared_sha, "actual": actual_sha, "target_record_id": target_id},
            )
        )

    # Action vs target_kind consistency
    if target_kind == RecordKind.CONFLICT and action != GovernanceAction.ADJUDICATE:
        issues.append(
            ValidationIssue(
                code="INVALID_GOVERNANCE_ACTION",
                severity="error",
                message=(
                    f"Conflict target {target_id!r} requires action='adjudicate', got {action!r}."
                ),
                record_id=rec_id,
                field_path="body.action",
            )
        )
    if target_kind != RecordKind.CONFLICT and action == GovernanceAction.ADJUDICATE:
        issues.append(
            ValidationIssue(
                code="INVALID_GOVERNANCE_ACTION",
                severity="error",
                message=(
                    f"Action 'adjudicate' is only valid for target_kind='conflict', "
                    f"got {target_kind!r}."
                ),
                record_id=rec_id,
                field_path="body.action",
            )
        )

    if action == GovernanceAction.ADJUDICATE:
        resolution = body.get("resolution", {})
        if resolution.get("disposition") == AdjudicationDisposition.SCOPED_EXCEPTION:
            exc_scope = resolution.get("exception_scope")
            if not isinstance(exc_scope, dict) or not exc_scope.get("repository"):
                issues.append(
                    ValidationIssue(
                        code="AMBIGUOUS_SCOPED_EXCEPTION",
                        severity="error",
                        message=(
                            "Adjudication with disposition='scoped-exception' must specify "
                            "an unambiguous 'resolution.exception_scope' with 'repository'."
                        ),
                        record_id=rec_id,
                        field_path="body.resolution.exception_scope",
                    )
                )

    # Authority vs proof consistency: local-owner-assertion must never claim verified_repository_approval=True
    authority = body.get("authority", {})
    proof = body.get("proof", {})
    auth_mode = authority.get("mode")
    if auth_mode == "local-owner-assertion" and proof.get("verified_repository_approval") is True:
        issues.append(
            ValidationIssue(
                code="FORGED_AUTHORITY_UPGRADE",
                severity="error",
                message=(
                    "A 'local-owner-assertion' decision must never set "
                    "proof.verified_repository_approval=true."
                ),
                record_id=rec_id,
                field_path="body.proof.verified_repository_approval",
            )
        )


def _validate_outcome_event(
    rec_id: str,
    body: dict[str, Any],
    record_digest: str,
    by_id: dict[str, RecordDict],
    seen_dedup_keys: dict[str, tuple[str, str]],
    assignments_by_pr: dict[tuple[str, str, str], tuple[str | None, str]],
    issues: list[ValidationIssue],
) -> None:
    pv_id = str(body["practice_version_id"])
    app_id = str(body["application_record_id"])

    pv = by_id.get(pv_id)
    if pv is None:
        issues.append(
            ValidationIssue(
                code="DANGLING_REFERENCE",
                severity="error",
                message=f"OutcomeEvent references missing practice_version_id {pv_id!r}.",
                record_id=rec_id,
                field_path="body.practice_version_id",
            )
        )
    elif pv["kind"] != RecordKind.PRACTICE_VERSION:
        issues.append(
            ValidationIssue(
                code="WRONG_REFERENCE_KIND",
                severity="error",
                message=(
                    f"OutcomeEvent practice_version_id {pv_id!r} points to kind {pv['kind']!r}, "
                    "expected 'practice_version'."
                ),
                record_id=rec_id,
                field_path="body.practice_version_id",
            )
        )

    app = by_id.get(app_id)
    if app is None:
        issues.append(
            ValidationIssue(
                code="DANGLING_REFERENCE",
                severity="error",
                message=f"OutcomeEvent references missing application_record_id {app_id!r}.",
                record_id=rec_id,
                field_path="body.application_record_id",
            )
        )
    elif app["kind"] != RecordKind.APPLICATION:
        issues.append(
            ValidationIssue(
                code="WRONG_REFERENCE_KIND",
                severity="error",
                message=(
                    f"OutcomeEvent application_record_id {app_id!r} points to kind {app['kind']!r}, "
                    "expected 'application'."
                ),
                record_id=rec_id,
                field_path="body.application_record_id",
            )
        )
    elif app["body"].get("practice_version_id") != pv_id:
        issues.append(
            ValidationIssue(
                code="APPLICATION_PRACTICE_MISMATCH",
                severity="error",
                message=(
                    f"OutcomeEvent application_record_id {app_id!r} belongs to practice_version "
                    f"{app['body'].get('practice_version_id')!r}, not {pv_id!r}."
                ),
                record_id=rec_id,
                field_path="body.application_record_id",
            )
        )

    dedup_key = str(body["dedup_key"])
    if dedup_key in seen_dedup_keys:
        prev_id, _ = seen_dedup_keys[dedup_key]
        issues.append(
            ValidationIssue(
                code="DUPLICATE_DEDUP_KEY",
                severity="error",
                message=f"Duplicate OutcomeEvent dedup_key {dedup_key!r} (already seen in {prev_id!r}).",
                record_id=rec_id,
                field_path="body.dedup_key",
            )
        )
    else:
        seen_dedup_keys[dedup_key] = (rec_id, record_digest)

    # Indeterminate outcomes must never be represented as passing/clean checks
    event_type = str(body["event_type"])
    details = body.get("details", {})
    if event_type == "indeterminate" and (
        details.get("status") in ("pass", "clean", "ok", "success")
        or details.get("passed") is True
    ):
        issues.append(
            ValidationIssue(
                code="INDETERMINATE_MARKED_SUCCESS",
                severity="error",
                message="An 'indeterminate' outcome_event must never be marked as passing/clean.",
                record_id=rec_id,
                field_path="body.details",
            )
        )

    # Experiment assignment invariant
    exp_id = body.get("experiment_id")
    intervention_events = {"alert", "action", "repeat-correction", "false-alert-adjudication"}
    if exp_id and event_type in intervention_events:
        key = (str(exp_id), str(body["repo_id"]), str(body["pr_id"]))
        if key not in assignments_by_pr:
            issues.append(
                ValidationIssue(
                    code="MISSING_EXPERIMENT_ASSIGNMENT",
                    severity="error",
                    message=(
                        f"OutcomeEvent {event_type!r} in experiment {exp_id!r} for "
                        f"{body['repo_id']}#{body['pr_id']} has no recorded 'assignment' event."
                    ),
                    record_id=rec_id,
                    field_path="body.experiment_id",
                )
            )
        else:
            arm, _ = assignments_by_pr[key]
            if arm == "control" and event_type == "alert" and details.get("published_to_pr") is True:
                issues.append(
                    ValidationIssue(
                        code="CONTROL_ARM_LEAKAGE",
                        severity="error",
                        message="Control-arm alert must never be published visibly to PR participants.",
                        record_id=rec_id,
                        field_path="body.details.published_to_pr",
                    )
                )
