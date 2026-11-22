"""Tests for Git-backed immutable storage and trusted-base mutation detection."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from onemind.engine import OneMindEngine
from onemind.protocol.canonical import canonical_sha256
from onemind.storage.base import ConflictingDedupKeyError, DuplicateRecordError
from onemind.storage.git_store import GitRecordStore


def _init_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    return repo


def _commit_all(repo: Path, msg: str) -> str:
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=repo, check=True, capture_output=True)
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    )
    return proc.stdout.strip()


def _fixture_records() -> list[dict]:
    root = Path(__file__).resolve().parents[1]
    return json.loads((root / "examples" / "records.json").read_text(encoding="utf-8"))


def test_git_store_append_only_and_trusted_base_verified(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    store = GitRecordStore(repo)
    records = _fixture_records()

    for rec in records[:6]:
        store.append(rec)

    base_commit = _commit_all(repo, "Initial immutable records")

    # Duplicate append must fail
    with pytest.raises(DuplicateRecordError):
        store.append(records[0])

    # Appending new record on top of trusted base is allowed and passes verification
    store.append(records[7])  # conflict
    _commit_all(repo, "Append conflict record")

    report = store.verify_against_trusted_base(base_commit)
    assert report.status == "verified"
    assert report.checked_files == 6
    assert not report.issues

    # Reading pinned commit returns exact 6 records from base_commit
    pinned = store.read_records_at_commit(base_commit)
    assert len(pinned) == 6
    assert {r["record_id"] for r in pinned} == {r["record_id"] for r in records[:6]}


def test_trusted_base_detects_modified_and_deleted_immutable_records(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    store = GitRecordStore(repo)
    records = _fixture_records()

    for rec in records[:4]:
        store.append(rec)
    base_commit = _commit_all(repo, "Base records")

    # Mutate one record on disk
    ev_path = store.record_path("evidence", "fixture-0001")
    mutated = json.loads(ev_path.read_text(encoding="utf-8"))
    mutated["body"]["redacted_snapshot"] = "Tampered text!"
    ev_path.write_text(json.dumps(mutated, indent=2) + "\n", encoding="utf-8")

    # Delete another record
    cand_path = store.record_path("practice_candidate", "fixture-0002")
    cand_path.unlink()

    report = store.verify_against_trusted_base(base_commit)
    assert report.status == "failed"
    codes = {i.code for i in report.issues}
    assert "IMMUTABLE_RECORD_MODIFIED" in codes
    assert "IMMUTABLE_RECORD_DELETED" in codes


def test_trusted_base_unavailable_vs_invalid_ref(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    store = GitRecordStore(repo)

    unavail = store.verify_against_trusted_base(None)
    assert unavail.status == "unavailable"
    assert unavail.reason is not None

    bad_ref = store.verify_against_trusted_base("nonexistent-tag-or-sha")
    assert bad_ref.status == "failed"
    assert any(i.code == "TRUSTED_BASE_UNRESOLVABLE" for i in bad_ref.issues)


def test_outcome_batch_dedup_and_conflict(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    store = GitRecordStore(repo)
    records = _fixture_records()
    for rec in records[:6]:
        store.append(rec)

    engine = OneMindEngine(store)
    event = copy.deepcopy(records[6])

    res1 = engine.observe(event, batch_id="batch-001")
    assert res1.appended is True
    assert res1.deduplicated is False
    manifest = json.loads((store.batches_dir / "batch-001" / "manifest.json").read_text())
    assert manifest["event_count"] == 1
    assert manifest["events"][0]["record_sha256"] == canonical_sha256(event)

    # Re-observing identical payload with same dedup_key is idempotent
    res2 = engine.observe(copy.deepcopy(event), batch_id="batch-002")
    assert res2.appended is False
    assert res2.deduplicated is True

    # Re-observing DIFFERENT payload with same dedup_key is rejected
    conflict_ev = copy.deepcopy(event)
    conflict_ev["record_id"] = "fixture-0007-conflict"
    conflict_ev["body"]["details"]["finding"] = "altered finding"
    with pytest.raises((ValueError, ConflictingDedupKeyError)):
        engine.observe(conflict_ev)
