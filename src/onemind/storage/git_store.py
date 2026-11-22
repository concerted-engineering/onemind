"""Git-backed immutable append-only record store (.onemind/records and .onemind/batches)."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence
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


class GitRecordStore(Store, OutcomeSink):
    """Canonical file-based store under `<repo>/.onemind/` backed by Git."""

    def __init__(self, repo_path: Path | str, *, store_rel_dir: str = ".onemind") -> None:
        self.repo_path = Path(repo_path).resolve()
        self.store_rel_dir = store_rel_dir
        self.store_dir = self.repo_path / store_rel_dir
        self.records_dir = self.store_dir / "records"
        self.batches_dir = self.store_dir / "batches"

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
                    _ensure_within_root(self.repo_path, p)
                    files.append(p)
        # Also include outcome_event records stored inside immutable batches
        if (kind is None or kind == RecordKind.OUTCOME_EVENT) and self.batches_dir.is_dir():
            for batch_dir in sorted(self.batches_dir.iterdir()):
                if not batch_dir.is_dir():
                    continue
                for p in sorted(batch_dir.glob("*.json")):
                    if p.name == "manifest.json":
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

    def list_records_raw_with_errors(self) -> tuple[list[RecordDict], list[tuple[Path, str]]]:
        """Load all record files without raising on malformed JSON, for `onemind validate`."""
        records: list[RecordDict] = []
        parse_errors: list[tuple[Path, str]] = []
        for path in self._iter_record_files(kind=None):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                parse_errors.append((path, str(exc)))
                continue
            if not isinstance(raw, dict):
                parse_errors.append((path, "Record file must contain a JSON object."))
                continue
            # Check path/kind/id consistency
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
        errors = validate_record_schema(record)
        if errors:
            raise StoreError(
                f"Refusing to write invalid record: {'; '.join(e.message for e in errors)}"
            )
        rec_id = str(record["record_id"])
        kind = str(record["kind"])
        if not is_safe_record_id(rec_id):
            raise StoreError(f"Unsafe record_id: {rec_id!r}")

        if self.get_record(rec_id) is not None:
            raise DuplicateRecordError(
                f"Record {rec_id!r} already exists; immutable records cannot be overwritten."
            )

        if kind == RecordKind.OUTCOME_EVENT:
            dedup_key = str(record["body"]["dedup_key"])
            existing = self._find_existing_dedup_key(dedup_key)
            if existing is not None:
                prev_id, prev_digest = existing
                if prev_digest != canonical_sha256(record["body"]):
                    raise ConflictingDedupKeyError(
                        f"dedup_key {dedup_key!r} already exists in {prev_id!r} with different payload."
                    )
                return prev_id

        target = self.record_path(kind, rec_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        _ensure_within_root(self.repo_path, target.parent)

        payload = json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        try:
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError as exc:
            raise DuplicateRecordError(f"Record file already exists: {target}") from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
        except Exception:
            if target.exists():
                target.unlink()
            raise
        return rec_id

    def append_batch(
        self,
        events: Sequence[RecordDict],
        *,
        batch_id: str | None = None,
    ) -> tuple[Path, list[str], list[str]]:
        """Atomically append an immutable batch of OutcomeEvent records with a manifest.

        Returns (batch_dir, appended_ids, deduplicated_existing_ids).
        """
        if not events:
            raise StoreError("Cannot write an empty outcome batch.")
        bid = batch_id or generate_record_id("batch")
        if not is_safe_record_id(bid):
            raise StoreError(f"Unsafe batch_id: {bid!r}")

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

        batch_dir.mkdir(parents=True, exist_ok=False)
        _ensure_within_root(self.repo_path, batch_dir)

        manifest_entries: list[dict[str, Any]] = []
        appended_ids: list[str] = []
        for ev in to_write:
            rec_id = str(ev["record_id"])
            ev_path = batch_dir / f"{rec_id}.json"
            payload = json.dumps(ev, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            ev_path.write_text(payload, encoding="utf-8")
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
        (batch_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
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
