"""Git-backed immutable append-only record store with writer locking, atomicity, and recovery."""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import shutil
import subprocess
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from onemind.adapters.interfaces import OutcomeSink, Store
from onemind.protocol.canonical import canonical_sha256
from onemind.protocol.models import (
    ALL_RECORD_KINDS,
    RecordDict,
    RecordKind,
    generate_record_id,
    is_safe_record_id,
    utc_now_iso,
)
from onemind.protocol.schema import validate_record_schema
from onemind.storage.base import (
    ConflictingDedupKeyError,
    DuplicateRecordError,
    StoreError,
)
from onemind.storage.mutation import (
    TrustedBaseReport,
    get_head_commit,
    git_ledger_sequence,
    is_git_repo,
    resolve_git_commit,
    verify_trusted_base,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[Path, threading.RLock] = {}


def _get_thread_lock(lock_key: Path) -> threading.RLock:
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(lock_key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[lock_key] = lock
        return lock


def _ensure_within_root(root: Path, target: Path) -> Path:
    """Resolve target and verify it stays strictly within root (blocks symlink/traversal escapes)."""
    resolved_root = root.resolve()
    resolved_target = target.resolve()
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        raise StoreError(
            f"Path {target} escapes repository root {resolved_root}."
        ) from exc
    return resolved_target


def _fsync_directory(directory: Path) -> None:
    """Best-effort fsync on a directory for crash durability."""
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


class GitRecordStore(Store, OutcomeSink):
    """Canonical file-based store under `<repo>/.onemind/` backed by Git.

    Hardening properties:
    - Cross-thread (`RLock`) and cross-process (`fcntl.flock`) writer synchronization.
    - Multi-record atomic transactions (`append_many`) with full staging and rollback.
    - Atomic batch directory staging (`append_batch`) with manifest SHA-256 verification.
    - Safe path containment blocking symlink and `..` traversal escapes.
    - Explicit corruption detection and safe staging recovery (`recover_staging`).
    - Read-only operations never create lock files or mutate the working tree.
    """

    def __init__(self, repo_path: Path | str, *, store_rel_dir: str = ".onemind") -> None:
        self.repo_path = Path(repo_path).resolve()
        self.store_rel_dir = store_rel_dir
        self.store_dir = self.repo_path / store_rel_dir
        self.records_dir = self.store_dir / "records"
        self.batches_dir = self.store_dir / "batches"
        self.ingestions_dir = self.store_dir / "ingestions"
        self.cache_dir = self.store_dir / "cache"
        self._lock_path = self.cache_dir / ".write.lock"

    @contextlib.contextmanager
    def _writer_lock(self) -> Iterator[None]:
        """Acquire both in-process thread lock and cross-process advisory file lock."""
        tlock = _get_thread_lock(self.store_dir)
        with tlock:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            _ensure_within_root(self.repo_path, self.cache_dir)
            fd = os.open(str(self._lock_path), os.O_RDWR | os.O_CREAT, 0o644)
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                if fcntl is not None:
                    with contextlib.suppress(OSError):
                        fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def record_path(self, kind: str, record_id: str) -> Path:
        if kind not in ALL_RECORD_KINDS:
            raise StoreError(f"Unknown record kind: {kind!r}")
        if not is_safe_record_id(record_id):
            raise StoreError(f"Unsafe record_id: {record_id!r}")
        return self.records_dir / kind / f"{record_id}.json"

    def _iter_record_files(self, kind: str | None = None) -> list[Path]:
        files: list[Path] = []
        if self.records_dir.is_dir():
            kinds = [kind] if kind is not None else sorted(ALL_RECORD_KINDS)
            for k in kinds:
                kdir = self.records_dir / k
                if not kdir.is_dir():
                    continue
                for p in sorted(kdir.glob("*.json")):
                    if p.name.startswith(".tmp-"):
                        continue
                    _ensure_within_root(self.repo_path, p)
                    files.append(p)
        # Include finalized outcome_event records stored inside immutable batches
        if (kind is None or kind == RecordKind.OUTCOME_EVENT) and self.batches_dir.is_dir():
            for batch_dir in sorted(self.batches_dir.iterdir()):
                if not batch_dir.is_dir() or batch_dir.name.startswith(".staging-"):
                    continue
                for p in sorted(batch_dir.glob("*.json")):
                    if p.name == "manifest.json" or p.name.startswith(".tmp-"):
                        continue
                    _ensure_within_root(self.repo_path, p)
                    files.append(p)
        return files

    def list_records(self, kind: str | None = None) -> list[RecordDict]:
        """Load all records from disk in deterministic ledger order."""
        records: list[RecordDict] = []
        for path in self._iter_record_files(kind=kind):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StoreError(f"Failed to parse record file {path}: {exc}") from exc
            if not isinstance(raw, dict):
                raise StoreError(f"Record file {path} does not contain a JSON object.")
            records.append(raw)

        seq = self.ledger_order()
        fallback_base = len(seq) + 1
        records.sort(
            key=lambda r: (
                seq.get(str(r.get("record_id", "")), fallback_base),
                str(r.get("record_id", "")),
            )
        )
        return records

    def verify_batch_manifests(self) -> list[tuple[Path, str, str]]:
        """Verify integrity of finalized batches and detect orphaned staging artifacts.

        Returns list of (path, error_code, message).
        """
        findings: list[tuple[Path, str, str]] = []
        if not self.batches_dir.is_dir():
            return findings

        for entry in sorted(self.batches_dir.iterdir()):
            if entry.name.startswith(".staging-"):
                findings.append(
                    (
                        entry,
                        "ORPHAN_STAGING_ARTIFACT",
                        f"Incomplete staging batch directory {entry.name!r} detected; run recover_staging() to clean up.",
                    )
                )
                continue
            if not entry.is_dir():
                continue

            manifest_path = entry / "manifest.json"
            if not manifest_path.is_file():
                findings.append(
                    (
                        manifest_path,
                        "CORRUPT_BATCH_MANIFEST",
                        f"Batch directory {entry.name!r} is missing manifest.json.",
                    )
                )
                continue

            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                findings.append(
                    (
                        manifest_path,
                        "CORRUPT_BATCH_MANIFEST",
                        f"Batch manifest {manifest_path} is not valid JSON: {exc}",
                    )
                )
                continue

            if not isinstance(manifest, dict) or not isinstance(manifest.get("events"), list):
                findings.append(
                    (
                        manifest_path,
                        "CORRUPT_BATCH_MANIFEST",
                        f"Batch manifest {manifest_path} missing 'events' list.",
                    )
                )
                continue

            listed_files: set[str] = set()
            for item in manifest["events"]:
                if not isinstance(item, dict) or not item.get("file") or not item.get("record_sha256"):
                    findings.append(
                        (
                            manifest_path,
                            "CORRUPT_BATCH_MANIFEST",
                            f"Batch manifest {manifest_path} contains malformed event entry.",
                        )
                    )
                    continue
                fname = str(item["file"])
                listed_files.add(fname)
                ev_path = entry / fname
                if not ev_path.is_file():
                    findings.append(
                        (
                            ev_path,
                            "CORRUPT_BATCH_MANIFEST",
                            f"Manifest lists missing batch event file {fname!r}.",
                        )
                    )
                    continue
                try:
                    ev_obj = json.loads(ev_path.read_text(encoding="utf-8"))
                    actual_sha = canonical_sha256(ev_obj)
                except (OSError, ValueError, TypeError) as exc:
                    findings.append(
                        (
                            ev_path,
                            "MALFORMED_RECORD_FILE",
                            f"Could not parse/canonicalize batch event {ev_path}: {exc}",
                        )
                    )
                    continue
                if actual_sha != item["record_sha256"]:
                    findings.append(
                        (
                            ev_path,
                            "BATCH_EVENT_DIGEST_MISMATCH",
                            (
                                f"Batch event {fname!r} canonical digest {actual_sha!r} "
                                f"does not match manifest digest {item['record_sha256']!r}."
                            ),
                        )
                    )

            for actual_file in sorted(entry.glob("*.json")):
                if actual_file.name == "manifest.json":
                    continue
                if actual_file.name not in listed_files:
                    findings.append(
                        (
                            actual_file,
                            "CORRUPT_BATCH_MANIFEST",
                            f"Unlisted event file {actual_file.name!r} present in batch {entry.name!r}.",
                        )
                    )
        return findings

    def list_records_raw_with_errors(self) -> tuple[list[RecordDict], list[tuple[Path, str]]]:
        """Load all record files without raising on malformed JSON, for `onemind validate`."""
        records: list[RecordDict] = []
        parse_errors: list[tuple[Path, str]] = []

        # Detect orphan temp files left in record directories by interrupted writes
        if self.records_dir.is_dir():
            for tmp_file in sorted(self.records_dir.rglob(".tmp-*")):
                parse_errors.append(
                    (
                        tmp_file,
                        f"Orphan temporary staging file {tmp_file.name!r} detected; run recover_staging().",
                    )
                )

        # Detect batch manifest corruption or orphan staging dirs
        for bpath, code, msg in self.verify_batch_manifests():
            parse_errors.append((bpath, f"[{code}] {msg}"))

        # Detect ingestion-run manifest corruption or orphan staging dirs
        for ipath, code, msg in self.verify_ingestion_manifests():
            parse_errors.append((ipath, f"[{code}] {msg}"))

        for path in self._iter_record_files(kind=None):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                parse_errors.append((path, str(exc)))
                continue
            if not isinstance(raw, dict):
                parse_errors.append((path, "Record file must contain a JSON object."))
                continue
            if path.parent.parent == self.records_dir:
                expected_kind = path.parent.name
                expected_id = path.stem
                if raw.get("kind") != expected_kind:
                    parse_errors.append(
                        (
                            path,
                            f"File located under kind directory {expected_kind!r} but record kind is {raw.get('kind')!r}.",
                        )
                    )
                if raw.get("record_id") != expected_id:
                    parse_errors.append(
                        (
                            path,
                            f"Filename stem {expected_id!r} does not match record_id {raw.get('record_id')!r}.",
                        )
                    )
            records.append(raw)
        return records, parse_errors

    def recover_staging(self) -> list[str]:
        """Safely remove incomplete `.tmp-*` and `.staging-*` artifacts left by crashed writers.

        Never touches finalized canonical record files or finalized batch directories.
        """
        cleaned: list[str] = []
        with self._writer_lock():
            if self.records_dir.is_dir():
                for tmp_file in sorted(self.records_dir.rglob(".tmp-*")):
                    if tmp_file.is_file():
                        _ensure_within_root(self.repo_path, tmp_file)
                        tmp_file.unlink()
                        cleaned.append(str(tmp_file.relative_to(self.repo_path)))
            if self.batches_dir.is_dir():
                for staging_dir in sorted(self.batches_dir.glob(".staging-*")):
                    if staging_dir.is_dir():
                        _ensure_within_root(self.repo_path, staging_dir)
                        shutil.rmtree(staging_dir)
                        cleaned.append(str(staging_dir.relative_to(self.repo_path)))
            if self.ingestions_dir.is_dir():
                for staging_dir in sorted(self.ingestions_dir.glob(".staging-*")):
                    if staging_dir.is_dir():
                        _ensure_within_root(self.repo_path, staging_dir)
                        shutil.rmtree(staging_dir)
                        cleaned.append(str(staging_dir.relative_to(self.repo_path)))
        return cleaned

    def append_ingestion_run(self, manifest: dict[str, Any]) -> Path:
        """Atomically persist an immutable IngestionRun manifest under `.onemind/ingestions/<run_id>/`."""
        run_id = manifest.get("run_id")
        if not isinstance(run_id, str) or not is_safe_record_id(run_id):
            raise StoreError(f"Invalid or unsafe ingestion run_id: {run_id!r}")
        if manifest.get("schema_version") != "0.1-draft-ingestion-run":
            raise StoreError(
                f"Unsupported ingestion run schema_version: {manifest.get('schema_version')!r}"
            )

        with self._writer_lock():
            run_dir = self.ingestions_dir / run_id
            if run_dir.exists():
                raise DuplicateRecordError(f"Ingestion run directory already exists: {run_dir}")

            self.ingestions_dir.mkdir(parents=True, exist_ok=True)
            _ensure_within_root(self.repo_path, self.ingestions_dir)
            staging_dir = self.ingestions_dir / f".staging-{run_id}-{secrets.token_hex(4)}"
            staging_dir.mkdir(parents=False, exist_ok=False)
            try:
                manifest_path = staging_dir / "manifest.json"
                payload = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
                with manifest_path.open("w", encoding="utf-8") as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                _fsync_directory(staging_dir)
                os.rename(str(staging_dir), str(run_dir))
                _fsync_directory(self.ingestions_dir)
            except Exception:
                shutil.rmtree(staging_dir, ignore_errors=True)
                raise
            return run_dir / "manifest.json"

    def list_ingestion_runs(self) -> list[dict[str, Any]]:
        """Load all finalized IngestionRun manifests in deterministic run_id order."""
        runs: list[dict[str, Any]] = []
        if not self.ingestions_dir.is_dir():
            return runs
        for entry in sorted(self.ingestions_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith(".staging-"):
                continue
            mpath = entry / "manifest.json"
            if not mpath.is_file():
                continue
            _ensure_within_root(self.repo_path, mpath)
            parsed = json.loads(mpath.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                runs.append(parsed)
        return runs

    def verify_ingestion_manifests(self) -> list[tuple[Path, str, str]]:
        """Verify integrity of persisted `.onemind/ingestions/<run_id>/manifest.json` files."""
        findings: list[tuple[Path, str, str]] = []
        if not self.ingestions_dir.is_dir():
            return findings

        for entry in sorted(self.ingestions_dir.iterdir()):
            if entry.name.startswith(".staging-"):
                findings.append(
                    (
                        entry,
                        "ORPHAN_STAGING_ARTIFACT",
                        f"Incomplete ingestion staging directory {entry.name!r}; run recover_staging() to clean up.",
                    )
                )
                continue
            if not entry.is_dir():
                continue

            mpath = entry / "manifest.json"
            if not mpath.is_file():
                findings.append(
                    (
                        mpath,
                        "CORRUPT_INGESTION_MANIFEST",
                        f"Ingestion directory {entry.name!r} is missing manifest.json.",
                    )
                )
                continue

            try:
                _ensure_within_root(self.repo_path, mpath)
                manifest = json.loads(mpath.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, StoreError) as exc:
                findings.append(
                    (
                        mpath,
                        "CORRUPT_INGESTION_MANIFEST",
                        f"Ingestion manifest {mpath} could not be parsed: {exc}",
                    )
                )
                continue

            if not isinstance(manifest, dict):
                findings.append(
                    (
                        mpath,
                        "CORRUPT_INGESTION_MANIFEST",
                        f"Ingestion manifest {mpath} must be a JSON object.",
                    )
                )
                continue

            if manifest.get("schema_version") != "0.1-draft-ingestion-run":
                findings.append(
                    (
                        mpath,
                        "UNSUPPORTED_INGESTION_SCHEMA",
                        f"Unsupported ingestion manifest schema_version {manifest.get('schema_version')!r}.",
                    )
                )
                continue

            if manifest.get("run_id") != entry.name:
                findings.append(
                    (
                        mpath,
                        "CORRUPT_INGESTION_MANIFEST",
                        f"Ingestion directory {entry.name!r} does not match manifest run_id {manifest.get('run_id')!r}.",
                    )
                )

            ev_digests = manifest.get("evidence_digests")
            if not isinstance(ev_digests, list):
                findings.append(
                    (
                        mpath,
                        "CORRUPT_INGESTION_MANIFEST",
                        f"Ingestion manifest {mpath} missing 'evidence_digests' list.",
                    )
                )
                continue

            for item in ev_digests:
                if not isinstance(item, dict) or not item.get("record_id") or not item.get("record_sha256"):
                    findings.append(
                        (
                            mpath,
                            "CORRUPT_INGESTION_MANIFEST",
                            f"Ingestion manifest {mpath} contains malformed evidence_digests entry.",
                        )
                    )
                    continue
                rid = str(item["record_id"])
                rec = self.get_record(rid)
                if rec is None:
                    findings.append(
                        (
                            mpath,
                            "INGESTION_EVIDENCE_MISSING",
                            f"Ingestion manifest {entry.name!r} cites missing evidence record {rid!r}.",
                        )
                    )
                    continue
                actual_sha = canonical_sha256(rec)
                if actual_sha != item["record_sha256"]:
                    findings.append(
                        (
                            mpath,
                            "INGESTION_EVIDENCE_DIGEST_MISMATCH",
                            (
                                f"Evidence {rid!r} canonical digest {actual_sha!r} does not match "
                                f"ingestion manifest digest {item['record_sha256']!r}."
                            ),
                        )
                    )
        return findings

    def get_record(self, record_id: str) -> RecordDict | None:
        if not is_safe_record_id(record_id):
            return None
        for k in sorted(ALL_RECORD_KINDS):
            candidate = self.records_dir / k / f"{record_id}.json"
            if candidate.is_file():
                _ensure_within_root(self.repo_path, candidate)
                loaded: RecordDict = json.loads(candidate.read_text(encoding="utf-8"))
                return loaded
        if self.batches_dir.is_dir():
            for batch_dir in sorted(self.batches_dir.iterdir()):
                if not batch_dir.is_dir() or batch_dir.name.startswith(".staging-"):
                    continue
                candidate = batch_dir / f"{record_id}.json"
                if candidate.is_file():
                    _ensure_within_root(self.repo_path, candidate)
                    loaded = json.loads(candidate.read_text(encoding="utf-8"))
                    return loaded
        return None

    def _find_existing_dedup_key(self, dedup_key: str) -> tuple[str, str] | None:
        for rec in self.list_records(kind=RecordKind.OUTCOME_EVENT):
            body = rec.get("body", {})
            if body.get("dedup_key") == dedup_key:
                return str(rec["record_id"]), canonical_sha256(body)
        return None

    def append(self, record: RecordDict) -> str:
        """Validate and atomically append a single immutable record."""
        ids = self.append_many([record])
        return ids[0]

    def append_many(self, records: Sequence[RecordDict]) -> list[str]:
        """Atomically validate, stage, and persist multiple immutable records under writer lock.

        All-or-nothing: if any record fails validation, collides with an existing ID or
        conflicting dedup_key, or encounters an I/O error during promotion, any files staged
        or linked during this transaction are rolled back completely.
        """
        if not records:
            return []

        with self._writer_lock():
            existing_ids: set[str] = set()
            existing_dedups: dict[str, tuple[str, str]] = {}
            for rec in self.list_records():
                rid = str(rec["record_id"])
                existing_ids.add(rid)
                if rec.get("kind") == RecordKind.OUTCOME_EVENT:
                    dk = str(rec["body"]["dedup_key"])
                    existing_dedups[dk] = (rid, canonical_sha256(rec["body"]))

            # Phase 1: Validate all records before touching disk
            plan: list[tuple[str, Path | None, str | None]] = []  # (result_id, target_path, payload)
            tx_ids: set[str] = set()
            tx_dedups: dict[str, tuple[str, str]] = {}

            for record in records:
                errors = validate_record_schema(record)
                if errors:
                    raise StoreError(
                        f"Refusing to write invalid record: {'; '.join(e.message for e in errors)}"
                    )
                rec_id = str(record["record_id"])
                kind = str(record["kind"])
                if not is_safe_record_id(rec_id):
                    raise StoreError(f"Unsafe record_id: {rec_id!r}")

                if rec_id in existing_ids or rec_id in tx_ids:
                    raise DuplicateRecordError(
                        f"Record {rec_id!r} already exists; immutable records cannot be overwritten."
                    )

                if kind == RecordKind.OUTCOME_EVENT:
                    dedup_key = str(record["body"]["dedup_key"])
                    body_digest = canonical_sha256(record["body"])
                    prior = tx_dedups.get(dedup_key) or existing_dedups.get(dedup_key)
                    if prior is not None:
                        prev_id, prev_digest = prior
                        if prev_digest != body_digest:
                            raise ConflictingDedupKeyError(
                                f"dedup_key {dedup_key!r} already exists in {prev_id!r} with different payload."
                            )
                        plan.append((prev_id, None, None))
                        continue
                    tx_dedups[dedup_key] = (rec_id, body_digest)

                target = self.record_path(kind, rec_id)
                if target.exists():
                    raise DuplicateRecordError(f"Record file already exists: {target}")

                payload = json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
                tx_ids.add(rec_id)
                plan.append((rec_id, target, payload))

            # Phase 2: Write fsynced temp files, then atomically link into place with rollback
            tx_token = secrets.token_hex(6)
            staged_temps: list[tuple[Path, Path]] = []  # (tmp_path, target_path)
            linked_targets: list[Path] = []
            touched_dirs: set[Path] = set()

            try:
                for _, maybe_target, maybe_payload in plan:
                    if maybe_target is None or maybe_payload is None:
                        continue
                    maybe_target.parent.mkdir(parents=True, exist_ok=True)
                    _ensure_within_root(self.repo_path, maybe_target.parent)
                    touched_dirs.add(maybe_target.parent)
                    tmp_path = maybe_target.parent / f".tmp-{os.getpid()}-{tx_token}-{maybe_target.name}"
                    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as fh:
                            fh.write(maybe_payload)
                            fh.flush()
                            os.fsync(fh.fileno())
                    except Exception:
                        with contextlib.suppress(OSError):
                            tmp_path.unlink()
                        raise
                    staged_temps.append((tmp_path, maybe_target))

                # Promote atomically using os.link (fails with FileExistsError if target exists)
                for tmp_path, target in staged_temps:
                    try:
                        os.link(str(tmp_path), str(target))
                    except FileExistsError as exc:
                        raise DuplicateRecordError(f"Record file already exists: {target}") from exc
                    linked_targets.append(target)

                # Cleanup temp files and fsync parent directories
                for tmp_path, _ in staged_temps:
                    with contextlib.suppress(OSError):
                        tmp_path.unlink()
                for d in touched_dirs:
                    _fsync_directory(d)

            except Exception:
                # Full rollback of any targets promoted in this failed transaction
                for target in reversed(linked_targets):
                    with contextlib.suppress(OSError):
                        target.unlink()
                for tmp_path, _ in staged_temps:
                    with contextlib.suppress(OSError):
                        tmp_path.unlink()
                raise

            return [rec_id for rec_id, _, _ in plan]

    def append_batch(
        self,
        events: Sequence[RecordDict],
        *,
        batch_id: str | None = None,
    ) -> tuple[Path, list[str], list[str]]:
        """Atomically stage and publish an immutable batch of OutcomeEvent records with a manifest.

        Returns (batch_dir, appended_ids, deduplicated_existing_ids).
        """
        if not events:
            raise StoreError("Cannot write an empty outcome batch.")
        bid = batch_id or generate_record_id("batch")
        if not is_safe_record_id(bid):
            raise StoreError(f"Unsafe batch_id: {bid!r}")

        with self._writer_lock():
            to_write: list[RecordDict] = []
            deduped: list[str] = []
            seen_in_batch: dict[str, str] = {}

            for ev in events:
                if ev.get("kind") != RecordKind.OUTCOME_EVENT:
                    raise StoreError("Outcome batches only accept 'outcome_event' records.")
                errors = validate_record_schema(ev)
                if errors:
                    raise StoreError(
                        f"Invalid outcome_event in batch: {'; '.join(e.message for e in errors)}"
                    )
                rec_id = str(ev["record_id"])
                if not is_safe_record_id(rec_id):
                    raise StoreError(f"Unsafe record_id: {rec_id!r}")
                dedup_key = str(ev["body"]["dedup_key"])
                body_digest = canonical_sha256(ev["body"])

                existing = self._find_existing_dedup_key(dedup_key)
                if existing is not None:
                    prev_id, prev_digest = existing
                    if prev_digest != body_digest:
                        raise ConflictingDedupKeyError(
                            f"Conflicting outcome_event for dedup_key {dedup_key!r} (existing {prev_id!r})."
                        )
                    deduped.append(prev_id)
                    continue

                if dedup_key in seen_in_batch:
                    if seen_in_batch[dedup_key] != body_digest:
                        raise ConflictingDedupKeyError(
                            f"Conflicting duplicate dedup_key {dedup_key!r} within same batch."
                        )
                    continue

                if self.get_record(rec_id) is not None:
                    raise DuplicateRecordError(f"Record {rec_id!r} already exists.")

                seen_in_batch[dedup_key] = body_digest
                to_write.append(ev)

            batch_dir = self.batches_dir / bid
            if not to_write:
                return batch_dir, [], deduped

            if batch_dir.exists():
                raise DuplicateRecordError(f"Batch directory already exists: {batch_dir}")

            self.batches_dir.mkdir(parents=True, exist_ok=True)
            _ensure_within_root(self.repo_path, self.batches_dir)
            staging_dir = self.batches_dir / f".staging-{bid}-{secrets.token_hex(4)}"
            staging_dir.mkdir(parents=False, exist_ok=False)

            manifest_entries: list[dict[str, Any]] = []
            appended_ids: list[str] = []
            try:
                for ev in to_write:
                    rec_id = str(ev["record_id"])
                    ev_path = staging_dir / f"{rec_id}.json"
                    payload = json.dumps(ev, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
                    with ev_path.open("w", encoding="utf-8") as fh:
                        fh.write(payload)
                        fh.flush()
                        os.fsync(fh.fileno())
                    manifest_entries.append(
                        {
                            "record_id": rec_id,
                            "file": ev_path.name,
                            "record_sha256": canonical_sha256(ev),
                            "dedup_key": str(ev["body"]["dedup_key"]),
                        }
                    )
                    appended_ids.append(rec_id)

                manifest = {
                    "batch_id": bid,
                    "created_at": utc_now_iso(),
                    "event_count": len(manifest_entries),
                    "events": manifest_entries,
                }
                manifest_path = staging_dir / "manifest.json"
                with manifest_path.open("w", encoding="utf-8") as fh:
                    fh.write(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                _fsync_directory(staging_dir)

                # Atomically publish completed batch directory
                os.rename(str(staging_dir), str(batch_dir))
                _fsync_directory(self.batches_dir)
            except Exception:
                shutil.rmtree(staging_dir, ignore_errors=True)
                raise

            return batch_dir, appended_ids, deduped

    def record_outcome(
        self, event: RecordDict, *, batch_id: str | None = None
    ) -> tuple[bool, str]:
        _, appended, deduped = self.append_batch([event], batch_id=batch_id)
        if appended:
            return True, appended[0]
        return False, deduped[0]

    def snapshot_commit(self) -> str | None:
        return get_head_commit(self.repo_path)

    def ledger_order(self) -> dict[str, int]:
        return git_ledger_sequence(self.repo_path, store_rel_dir=self.store_rel_dir)

    def verify_against_trusted_base(self, trusted_base: str | None) -> TrustedBaseReport:
        return verify_trusted_base(
            self.repo_path, trusted_base, store_rel_dir=self.store_rel_dir
        )

    def read_records_at_commit(self, commit_ref: str) -> list[RecordDict]:
        """Read canonical records directly from a pinned Git commit without modifying worktree."""
        if not is_git_repo(self.repo_path):
            raise StoreError(f"{self.repo_path} is not a Git repository.")
        resolved = resolve_git_commit(self.repo_path, commit_ref)
        if resolved is None:
            raise StoreError(f"Could not resolve Git commit ref {commit_ref!r}.")
        ls_proc = subprocess.run(
            [
                "git",
                "ls-tree",
                "-r",
                "-z",
                "--name-only",
                resolved,
                "--",
                f"{self.store_rel_dir}/records",
                f"{self.store_rel_dir}/batches",
            ],
            cwd=str(self.repo_path),
            capture_output=True,
            check=False,
        )
        if ls_proc.returncode != 0:
            raise StoreError(ls_proc.stderr.decode("utf-8", errors="replace"))

        records: list[RecordDict] = []
        for raw_path in ls_proc.stdout.split(b"\x00"):
            if not raw_path:
                continue
            rel = raw_path.decode("utf-8", errors="surrogateescape")
            if not rel.endswith(".json") or rel.endswith("/manifest.json"):
                continue
            show = subprocess.run(
                ["git", "show", f"{resolved}:{rel}"],
                cwd=str(self.repo_path),
                capture_output=True,
                check=False,
            )
            if show.returncode != 0:
                raise StoreError(f"Failed to read {rel} at {resolved}")
            parsed = json.loads(show.stdout.decode("utf-8"))
            if isinstance(parsed, dict):
                records.append(parsed)
        records.sort(key=lambda r: str(r.get("record_id", "")))
        return records
