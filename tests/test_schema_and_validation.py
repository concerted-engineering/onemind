"""Tests for JSON Schema validation and semantic cross-record validation."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from onemind.protocol.canonical import canonical_sha256
from onemind.protocol.schema import validate_record_schema
from onemind.protocol.validator import validate_records


def _load_fixture_records() -> list[dict]:
    repo_root = Path(__file__).resolve().parents[1]
    return json.loads((repo_root / "examples" / "records.json").read_text(encoding="utf-8"))


def test_bundled_schema_matches_spec_schema() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    spec_text = (repo_root / "spec" / "record.schema.json").read_text(encoding="utf-8")
    pkg_text = (
        repo_root / "src" / "onemind" / "protocol" / "record.schema.json"
    ).read_text(encoding="utf-8")
    assert spec_text == pkg_text


def test_example_records_validate_cleanly() -> None:
    records = _load_fixture_records()
    report = validate_records(records)
    assert report.valid, [i.to_dict() for i in report.issues]
    assert report.record_count == 8
    assert report.counts_by_kind["governance_decision"] == 2
    assert report.historical_verification["status"] == "unavailable"


def test_rejects_unsupported_schema_version_and_unknown_kind() -> None:
    records = _load_fixture_records()
    bad_ver = copy.deepcopy(records[0])
    bad_ver["schema_version"] = "0.2-future"
    errs = validate_record_schema(bad_ver)
    assert any(e.code == "UNSUPPORTED_SCHEMA_VERSION" for e in errs)

    bad_kind = copy.deepcopy(records[0])
    bad_kind["kind"] = "policy_override"
    errs2 = validate_record_schema(bad_kind)
    assert any(e.code == "UNKNOWN_RECORD_KIND" for e in errs2)


def test_rejects_invalid_timestamp_format() -> None:
    records = _load_fixture_records()
    bad_ts = copy.deepcopy(records[0])
    bad_ts["created_at"] = "2026-11-20 00:00:00"  # missing T and timezone
    errs = validate_record_schema(bad_ts)
    assert errs


def test_rejects_duplicate_ids_and_dangling_references() -> None:
    records = _load_fixture_records()
    dup = copy.deepcopy(records)
    dup.append(copy.deepcopy(records[0]))
    report = validate_records(dup)
    assert not report.valid
    assert any(i.code == "DUPLICATE_RECORD_ID" for i in report.errors)

    dangling = [r for r in records if r["record_id"] != "fixture-0001"]
    report2 = validate_records(dangling)
    assert not report2.valid
    assert any(i.code == "DANGLING_REFERENCE" for i in report2.errors)


def test_rejects_incorrect_approval_digest() -> None:
    records = _load_fixture_records()
    for r in records:
        if r["record_id"] == "fixture-0004":
            r["body"]["target_sha256"] = "0" * 64
    report = validate_records(records)
    assert not report.valid
    assert any(i.code == "TARGET_DIGEST_MISMATCH" for i in report.errors)


def test_rejects_agent_governance_decision_and_forged_upgrade() -> None:
    records = _load_fixture_records()
    for r in records:
        if r["record_id"] == "fixture-0004":
            r["actor"] = {"type": "agent", "id": "coding-agent-1"}
            r["body"]["proof"]["verified_repository_approval"] = True
    report = validate_records(records)
    assert not report.valid
    codes = {i.code for i in report.errors}
    assert "UNAUTHORIZED_DECISION_ACTOR" in codes
    assert "FORGED_AUTHORITY_UPGRADE" in codes


def test_rejects_application_scope_overreach() -> None:
    records = _load_fixture_records()
    for r in records:
        if r["record_id"] == "fixture-0005":
            r["body"]["applicability"]["paths"] = ["**"]
            new_sha = canonical_sha256(r)
        if r["record_id"] == "fixture-0006":
            r["body"]["target_sha256"] = new_sha
    report = validate_records(records)
    assert not report.valid
    assert any(i.code == "APPLICATION_SCOPE_OVERREACH" for i in report.errors)


def test_rejects_indeterminate_outcome_marked_success() -> None:
    records = _load_fixture_records()
    records.append(
        {
            "schema_version": "0.1-draft",
            "record_id": "fixture-0009",
            "kind": "outcome_event",
            "created_at": "2026-11-20T01:00:00Z",
            "actor": {"type": "service", "id": "checker"},
            "body": {
                "event_type": "indeterminate",
                "repo_id": "sample/api-compat-demo",
                "pr_id": "synthetic-2",
                "head_sha": "abc123",
                "practice_version_id": "fixture-0003",
                "application_record_id": "fixture-0005",
                "checker_version": "0.1",
                "observed_at": "2026-11-20T01:00:00Z",
                "dedup_key": "synthetic-2:indeterminate",
                "details": {"status": "pass"},
            },
        }
    )
    report = validate_records(records)
    assert not report.valid
    assert any(i.code == "INDETERMINATE_MARKED_SUCCESS" for i in report.errors)


def test_rejects_unsafe_record_id_traversal() -> None:
    records = _load_fixture_records()
    bad = copy.deepcopy(records[0])
    bad["record_id"] = "../escape"
    report = validate_records([bad])
    assert not report.valid
    assert any(i.code == "UNSAFE_RECORD_ID" for i in report.errors)


def test_rejects_application_version_gap_and_invalid_previous_version_id() -> None:
    records = _load_fixture_records()[:6]
    # Add application v3 without v2 -> INVALID_APPLICATION_VERSION_CHAIN
    app_v3 = copy.deepcopy(records[4])
    app_v3["record_id"] = "app-v3"
    app_v3["body"]["application_version"] = 3
    report = validate_records([*records, app_v3])
    assert not report.valid
    assert any(i.code == "INVALID_APPLICATION_VERSION_CHAIN" for i in report.errors)


def test_rejects_backdated_governance_and_incomplete_conflict_and_ambiguous_scope() -> None:
    records = _load_fixture_records()
    # Backdated governance decision (effective_at < target.created_at)
    backdated = copy.deepcopy(records)
    backdated[3]["body"]["effective_at"] = "2026-11-19T00:00:00Z"
    r1 = validate_records(backdated)
    assert not r1.valid
    assert any(i.code == "BACKDATED_GOVERNANCE_DECISION" for i in r1.errors)

    # Conflict missing detector name/version
    bad_conf = copy.deepcopy(records)
    bad_conf[7]["body"]["detector"] = {}
    r2 = validate_records(bad_conf)
    assert not r2.valid
    assert any(i.code == "INCOMPLETE_CONFLICT_DETECTOR" for i in r2.errors)

    # Ambiguous scope with unknown constraint key -> AMBIGUOUS_SCOPE
    amb_scope = copy.deepcopy(records[:4])
    amb_scope[2]["body"]["scope"]["branch_pattern"] = "release/*"
    amb_scope[3]["body"]["target_sha256"] = canonical_sha256(amb_scope[2])
    r3 = validate_records(amb_scope)
    assert not r3.valid
    assert any(i.code == "AMBIGUOUS_SCOPE" for i in r3.errors)


def test_rejects_outcome_provenance_mismatches_and_assignment_ordering() -> None:
    records = _load_fixture_records()[:7]
    # Wrong checker_version and wrong repo_id and predating application
    bad_out = copy.deepcopy(records)
    bad_out[6]["body"]["checker_version"] = "9.9.9"
    bad_out[6]["body"]["repo_id"] = "other/repo"
    bad_out[6]["body"]["observed_at"] = "2026-11-19T00:00:00Z"
    r1 = validate_records(bad_out)
    assert not r1.valid
    codes = {i.code for i in r1.errors}
    assert "OUTCOME_CHECKER_VERSION_MISMATCH" in codes
    assert "OUTCOME_REPOSITORY_MISMATCH" in codes
    assert "OUTCOME_PREDATES_APPLICATION" in codes

    # Experiment assignment recorded AFTER alert -> ASSIGNMENT_AFTER_INTERVENTION
    exp_recs = copy.deepcopy(records[:6])
    assign_late = copy.deepcopy(records[6])
    assign_late["record_id"] = "ev-assign-late"
    assign_late["body"]["event_type"] = "assignment"
    assign_late["body"]["experiment_id"] = "exp-order"
    assign_late["body"]["observed_at"] = "2026-11-20T05:00:00Z"
    assign_late["body"]["dedup_key"] = "exp-order:assign"
    assign_late["body"]["details"] = {"arm": "treatment"}

    alert_early = copy.deepcopy(records[6])
    alert_early["record_id"] = "ev-alert-early"
    alert_early["body"]["event_type"] = "alert"
    alert_early["body"]["experiment_id"] = "exp-order"
    alert_early["body"]["observed_at"] = "2026-11-20T04:00:00Z"
    alert_early["body"]["dedup_key"] = "exp-order:alert"
    r2 = validate_records([*exp_recs, assign_late, alert_early])
    assert not r2.valid
    assert any(i.code == "ASSIGNMENT_AFTER_INTERVENTION" for i in r2.errors)
