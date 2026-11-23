"""Tests for Git-backed immutable storage, atomicity, locking, recovery, and trusted-base checks."""

from __future__ import annotations

import copy
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
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

    store.append_many(records[:6])
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

    # Reading pinned commit replays exact 6 records from base_commit
    pinned = store.read_records_at_commit(base_commit)
    assert len(pinned) == 6
    assert {r["record_id"] for r in pinned} == {r["record_id"] for r in records[:6]}


def test_multi_record_atomicity_rolls_back_on_failure(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    store = GitRecordStore(repo)
    records = _fixture_records()

    # Pre-seed fixture-0001
    store.append(records[0])

    # Attempt atomic write of [new_ev, duplicate fixture-0001] -> must roll back completely!
    new_ev = copy.deepcopy(records[0])
    new_ev["record_id"] = "ev-atomic-new"
    with pytest.raises(DuplicateRecordError):
        store.append_many([new_ev, records[0]])

    # Neither new_ev nor any .tmp-* file may exist on disk
    assert store.get_record("ev-atomic-new") is None
    assert list(store.records_dir.rglob(".tmp-*")) == []


def test_concurrent_writer_synchronization(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    store = GitRecordStore(repo)
    template = _fixture_records()[0]

    def _write_one(idx: int) -> str:
        rec = copy.deepcopy(template)
        rec["record_id"] = f"ev-concurrent-{idx:03d}"
        return store.append(rec)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_write_one, range(20)))

    assert len(set(results)) == 20
    assert len(store.list_records()) == 20


def test_corruption_detection_and_safe_staging_recovery(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    store = GitRecordStore(repo)
    records = _fixture_records()
    store.append_many(records[:6])

    # Write a valid outcome batch
    batch_dir, appended, _ = store.append_batch([records[6]], batch_id="batch-ok")
    assert appended == ["fixture-0007"]
    assert store.verify_batch_manifests() == []

    # Simulate crashed writer artifacts (.tmp-* and .staging-*)
    orphan_tmp = store.records_dir / "evidence" / ".tmp-999-crashed-ev.json"
    orphan_tmp.write_text("{partial", encoding="utf-8")
    orphan_staging = store.batches_dir / ".staging-crashed-batch"
    orphan_staging.mkdir(parents=True)
    (orphan_staging / "partial.json").write_text("{", encoding="utf-8")

    # list_records_raw_with_errors flags both artifacts
    _, errs = store.list_records_raw_with_errors()
    assert len(errs) >= 2

    # recover_staging removes ONLY incomplete staging/tmp artifacts, preserving canonical records
    cleaned = store.recover_staging()
    assert len(cleaned) == 2
    assert not orphan_tmp.exists()
    assert not orphan_staging.exists()
    assert len(store.list_records()) == 7

    # Tampering with finalized batch event breaks manifest SHA-256 verification
    ev_file = batch_dir / "fixture-0007.json"
    tampered = json.loads(ev_file.read_text(encoding="utf-8"))
    tampered["body"]["details"]["finding"] = "tampered!"
    ev_file.write_text(json.dumps(tampered, indent=2) + "\n", encoding="utf-8")

    manifest_errs = store.verify_batch_manifests()
    assert any(code == "BATCH_EVENT_DIGEST_MISMATCH" for _, code, _ in manifest_errs)


def test_trusted_base_detects_modified_and_deleted_immutable_records_committed_and_uncommitted(
    tmp_path: Path,
) -> None:
    repo = _init_git_repo(tmp_path)
    store = GitRecordStore(repo)
    records = _fixture_records()

    store.append_many(records[:4])
    base_commit = _commit_all(repo, "Base records")

    # Mutate one record and delete another, then COMMIT the unauthorized history rewrite
    ev_path = store.record_path("evidence", "fixture-0001")
    mutated = json.loads(ev_path.read_text(encoding="utf-8"))
    mutated["body"]["redacted_snapshot"] = "Tampered text!"
    ev_path.write_text(json.dumps(mutated, indent=2) + "\n", encoding="utf-8")

    cand_path = store.record_path("practice_candidate", "fixture-0002")
    cand_path.unlink()
    _commit_all(repo, "Unauthorized edit and deletion")

    report = store.verify_against_trusted_base(base_commit)
    assert report.status == "failed"
    codes = {i.code for i in report.issues}
    assert "IMMUTABLE_RECORD_MODIFIED" in codes
    assert "IMMUTABLE_RECORD_DELETED" in codes

    # Replay directly from trusted_base still yields untouched original 4 records
    replayed = store.read_records_at_commit(base_commit)
    assert len(replayed) == 4
    replayed_ev = next(r for r in replayed if r["record_id"] == "fixture-0001")
    assert replayed_ev["body"]["redacted_snapshot"] == records[0]["body"]["redacted_snapshot"]


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
    store.append_many(records[:6])

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
