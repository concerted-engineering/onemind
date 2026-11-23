"""OneMind CLI (`onemind`): offline, local-first engineering-intelligence commands.

Exit codes:
  0 — success / valid / check passed
  1 — validated finding (check failed or validation errors detected)
  2 — invalid CLI usage, bad input, or security/path violation
  3 — incomplete data, indeterminate result, or unknown checker implementation
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from onemind import __version__
from onemind.adapters.local_files import (
    CAPTURE_MODE_LEGEND,
    DEFAULT_MAX_FILE_BYTES,
    _is_within_root,
    inspect_source_lines,
    parse_source_spec,
    scan_repository,
)
from onemind.api import propose_from_source
from onemind.checks.registry import list_builtin_checkers, resolve_checker
from onemind.governance.policy import load_policy
from onemind.governance.projection import project_effective_state
from onemind.protocol.canonical import canonical_sha256, excerpt_sha256
from onemind.protocol.models import (
    PROTOCOL_STATUS,
    SCHEMA_VERSION,
    AuthorityClass,
    RecordDict,
    RecordKind,
)
from onemind.protocol.validator import ValidationIssue, validate_records
from onemind.storage.base import StoreError
from onemind.storage.git_store import GitRecordStore
from onemind.storage.mutation import get_head_commit

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_INVALID = 2
EXIT_INDETERMINATE = 3


def _emit_json(data: Any) -> None:
    sys.stdout.write(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def _emit_error(message: str, *, as_json: bool, code: str = "CLI_ERROR") -> None:
    if as_json:
        _emit_json({"error": {"code": code, "message": message}})
    else:
        sys.stderr.write(f"error [{code}]: {message}\n")


def _load_records_for_repo(
    repo_path: Path, records_file: str | None = None
) -> tuple[list[RecordDict], list[ValidationIssue], str | None]:
    """Load records either from an explicit JSON file or from `<repo>/.onemind/`."""
    issues: list[ValidationIssue] = []
    if records_file is not None:
        rpath = Path(records_file)
        if not rpath.is_file():
            raise FileNotFoundError(f"Records file not found: {rpath}")
        raw = json.loads(rpath.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            records = [raw]
        elif isinstance(raw, list):
            records = raw
        else:
            raise ValueError(f"Records file {rpath} must contain a JSON object or array.")
        return records, issues, None

    store = GitRecordStore(repo_path)
    records, parse_errors = store.list_records_raw_with_errors()
    for path, err in parse_errors:
        issues.append(
            ValidationIssue(
                code="MALFORMED_RECORD_FILE",
                severity="error",
                message=f"Malformed record file {path}: {err}",
                record_id=path.stem,
            )
        )
    return records, issues, store.snapshot_commit()


# ---------------------------------------------------------------------------
# Subcommand: scan
# ---------------------------------------------------------------------------
def _cmd_scan(args: argparse.Namespace) -> int:
    try:
        report = scan_repository(
            args.repo,
            include_paths=args.include or (),
            max_file_bytes=args.max_bytes,
        )
    except (FileNotFoundError, OSError) as exc:
        _emit_error(str(exc), as_json=args.json, code="SCAN_INPUT_ERROR")
        return EXIT_INVALID

    if args.json:
        _emit_json(report.to_dict())
        return EXIT_OK

    print(f"OneMind scan ({SCHEMA_VERSION} {PROTOCOL_STATUS}, offline, read-only)")
    print(f"Repository: {report.repo_path}")
    if report.head_commit:
        print(f"Git HEAD:   {report.head_commit[:12]}")
    print(
        f"Sources:    {len(report.sources)} discovered "
        f"({sum(1 for s in report.sources if s.source_state == 'available')} available)"
    )
    print(
        f"Statements: {len(report.candidate_statements)} heuristic normative statement(s) "
        "(capture_mode=heuristic-scan; unverified source evidence)"
    )

    if report.sources:
        print("\nSource inventory:")
        for item in report.sources:
            digest = item.excerpt_sha256[:12] if item.excerpt_sha256 else "n/a"
            stmt_cnt = item.metadata.get("candidate_statement_count", 0)
            lines_rng = (
                f"L{item.line_start}-L{item.line_end}"
                if item.line_start is not None and item.line_end is not None
                else "n/a"
            )
            print(
                f"  - [{item.source_type}] {item.source_id} ({lines_rng}, "
                f"state={item.source_state}, sha256={digest}, statements={stmt_cnt}, author={item.source_author})"
            )

    if report.candidate_statements:
        print("\nPer-line heuristic statements (heuristic-scan, unverified — NOT active practices):")
        for stmt in report.candidate_statements[:10]:
            preview = stmt.text if len(stmt.text) <= 90 else stmt.text[:87] + "..."
            print(
                f"  - {stmt.source_path}:{stmt.line_number} "
                f"[{stmt.matched_signal}, sha256={stmt.excerpt_sha256[:12]}]: {preview}"
            )
        if len(report.candidate_statements) > 10:
            print(f"  ... and {len(report.candidate_statements) - 10} more (use --json for full list)")

    print("\nCapture-mode distinction:")
    print(f"  * heuristic-scan:   {CAPTURE_MODE_LEGEND['heuristic-scan']}")
    print(f"  * explicit-capture: {CAPTURE_MODE_LEGEND['explicit-capture']}")

    if report.existing_store_summary.get("present"):
        print(
            f"\nCanonical store: .onemind/ detected ({report.existing_store_summary['record_files']} record file(s)). "
            "Run `onemind validate` to verify."
        )

    if report.warnings:
        print("\nWarnings:")
        for w in report.warnings:
            print(f"  ! {w}")

    print("\n" + " ".join(report.assurances))
    return EXIT_OK


# ---------------------------------------------------------------------------
# Subcommand: inspect
# ---------------------------------------------------------------------------
def _detect_record_capture_mode(rec: RecordDict) -> str:
    kind = rec.get("kind")
    ext_mode = rec.get("extensions", {}).get("source_provenance", {}).get("capture_mode")
    if isinstance(ext_mode, str):
        return ext_mode
    if kind == RecordKind.PRACTICE_CANDIDATE:
        gen = rec.get("body", {}).get("generator", {})
        if isinstance(gen.get("capture_mode"), str):
            return str(gen["capture_mode"])
        if gen.get("name") in ("manual-capture", "onemind-cli-propose", "onemind-explicit-capture"):
            return "explicit-capture"
        return f"adapter:{gen.get('name', 'unknown')}"
    if kind == RecordKind.EVIDENCE:
        src_type = rec.get("body", {}).get("source_type")
        if src_type == "local-file":
            return "explicit-capture" if rec.get("actor", {}).get("type") == "human" else "heuristic-scan"
        return f"adapter:{src_type}"
    return "governed-record"


def _check_live_source_drift(repo_root: Path, ev_body: dict[str, Any]) -> dict[str, Any]:
    """Read-only check whether a local-file Evidence excerpt still matches current disk content."""
    if ev_body.get("source_type") != "local-file":
        return {"status": "not-applicable", "reason": "Non-local-file evidence source."}
    source_id = str(ev_body.get("source_id", ""))
    if not source_id:
        return {"status": "not-checked", "reason": "Missing source_id."}
    try:
        info = inspect_source_lines(repo_root, source_id)
    except (FileNotFoundError, ValueError, OSError) as exc:
        return {"status": "source-unavailable-on-disk", "reason": str(exc)}

    disk_sha = excerpt_sha256(str(info["excerpt"]))
    recorded_sha = str(ev_body.get("excerpt_sha256", ""))
    if disk_sha == recorded_sha:
        return {"status": "matches-disk", "disk_sha256": disk_sha}
    return {
        "status": "source-drifted",
        "recorded_sha256": recorded_sha,
        "disk_sha256": disk_sha,
        "reason": "Current source line(s) on disk differ from recorded evidence excerpt_sha256.",
    }


def _inspect_source_file(
    repo_root: Path,
    source_spec: str,
    records: list[RecordDict],
) -> dict[str, Any]:
    info = inspect_source_lines(repo_root, source_spec)
    rel_posix = str(info["source_path"])

    linked_evidence: list[dict[str, Any]] = []
    linked_ev_ids: set[str] = set()
    for r in records:
        if r.get("kind") != RecordKind.EVIDENCE:
            continue
        eb = r.get("body", {})
        if str(eb.get("source_id", "")).startswith(rel_posix) or rel_posix in str(
            eb.get("source_uri", "")
        ):
            rid = str(r["record_id"])
            linked_ev_ids.add(rid)
            linked_evidence.append(
                {
                    "record_id": rid,
                    "source_uri": eb.get("source_uri"),
                    "capture_mode": _detect_record_capture_mode(r),
                    "excerpt_sha256": eb.get("excerpt_sha256"),
                }
            )

    linked_candidates = [
        {
            "record_id": r["record_id"],
            "statement": r["body"]["statement"],
            "capture_mode": _detect_record_capture_mode(r),
            "authority_class": AuthorityClass.INERT_CANDIDATE.value,
        }
        for r in records
        if r.get("kind") == RecordKind.PRACTICE_CANDIDATE
        and any(eid in linked_ev_ids for eid in r.get("body", {}).get("evidence_ids", []))
    ]

    return {
        "target_type": "source-document",
        "source_path": rel_posix,
        "source_uri": info["source_uri"],
        "line_start": info["line_start"],
        "line_end": info["line_end"],
        "file_sha256": info["file_sha256"],
        "excerpt_sha256": info["excerpt_sha256"],
        "source_revision": info["source_revision"],
        "excerpt": info["excerpt"],
        "lines": [lp.to_dict() for lp in info["lines"]],
        "owner": info["owner"],
        "authority_class": AuthorityClass.UNVERIFIED_SOURCE.value,
        "capture_mode_legend": dict(CAPTURE_MODE_LEGEND),
        "normative_statement": None,
        "heuristic_statements": [s.to_dict() for s in info["heuristic_statements"]],
        "linked_evidence_ids": sorted(linked_ev_ids),
        "linked_evidence": linked_evidence,
        "linked_candidates": linked_candidates,
        "explanation": (
            "Raw documentation is unverified source material, NOT an approved or active practice. "
            "Heuristic scan statements are automated unverified discoveries; use `onemind propose` "
            "to explicitly capture an inert source-linked PracticeCandidate."
        ),
    }


def _inspect_record(
    rec: RecordDict,
    all_records: list[RecordDict],
    *,
    repo_root: Path,
    as_of: str | None,
    allow_local: bool,
) -> dict[str, Any]:
    rec_id = str(rec["record_id"])
    kind = str(rec["kind"])
    body = rec["body"]
    by_id = {str(r["record_id"]): r for r in all_records if isinstance(r, dict) and "record_id" in r}

    policy = load_policy(repo_root).with_local_assertions(allow_local)
    eff = project_effective_state(
        all_records,
        as_of=as_of,
        policy=policy,
        commit_ref=get_head_commit(repo_root),
    )

    evidence_ids: list[str] = []
    if kind == RecordKind.EVIDENCE:
        evidence_ids = [rec_id]
    elif kind in (RecordKind.PRACTICE_CANDIDATE, RecordKind.PRACTICE_VERSION, RecordKind.CONFLICT):
        evidence_ids = list(body.get("evidence_ids", []))
    elif kind == RecordKind.APPLICATION:
        pv = by_id.get(str(body.get("practice_version_id", "")))
        if pv is not None:
            evidence_ids = list(pv.get("body", {}).get("evidence_ids", []))
    elif kind == RecordKind.GOVERNANCE_DECISION:
        target = by_id.get(str(body.get("target_record_id", "")))
        if target is not None:
            evidence_ids = list(target.get("body", {}).get("evidence_ids", []))

    evidence_details = []
    for ev_id in evidence_ids:
        ev = by_id.get(ev_id)
        if ev is not None:
            eb = ev["body"]
            src_prov = ev.get("extensions", {}).get("source_provenance", {})
            evidence_details.append(
                {
                    "record_id": ev_id,
                    "source_type": eb.get("source_type"),
                    "source_uri": eb.get("source_uri"),
                    "source_id": eb.get("source_id"),
                    "source_state": eb.get("source_state"),
                    "excerpt_sha256": eb.get("excerpt_sha256"),
                    "redacted_snapshot": eb.get("redacted_snapshot"),
                    "capture_mode": _detect_record_capture_mode(ev),
                    "line_provenance": src_prov.get("lines", []),
                    "disk_verification": _check_live_source_drift(repo_root, eb),
                }
            )
        else:
            evidence_details.append({"record_id": ev_id, "missing": True})

    decisions_targeting = [
        {
            "record_id": r["record_id"],
            "action": r["body"]["action"],
            "effective_at": r["body"]["effective_at"],
            "authority_mode": r["body"].get("authority", {}).get("mode"),
            "subject": r["body"].get("authority", {}).get("subject"),
            "verified_repository_approval": bool(
                r["body"].get("proof", {}).get("verified_repository_approval")
            ),
        }
        for r in all_records
        if r.get("kind") == RecordKind.GOVERNANCE_DECISION
        and r.get("body", {}).get("target_record_id") == rec_id
    ]

    related_apps = [
        r["record_id"]
        for r in all_records
        if r.get("kind") == RecordKind.APPLICATION
        and r.get("body", {}).get("practice_version_id") == rec_id
    ]
    related_conflicts = [
        r["record_id"]
        for r in all_records
        if r.get("kind") == RecordKind.CONFLICT
        and rec_id in (r.get("body", {}).get("left_record_id"), r.get("body", {}).get("right_record_id"))
    ]

    authority_class = AuthorityClass.INERT_EVIDENCE.value
    lifecycle = "inert"
    verified_repo = False
    if kind == RecordKind.PRACTICE_CANDIDATE:
        authority_class = AuthorityClass.INERT_CANDIDATE.value
        lifecycle = "inert"
    elif kind == RecordKind.PRACTICE_VERSION:
        st = eff.practice_states.get(rec_id)
        if st is not None:
            authority_class = st.authority_class.value
            lifecycle = st.lifecycle
            verified_repo = st.verified_repository_approval
    elif kind == RecordKind.APPLICATION:
        st = eff.application_states.get(rec_id)
        if st is not None:
            authority_class = st.authority_class.value
            lifecycle = st.lifecycle
            verified_repo = st.verified_repository_approval
    elif kind == RecordKind.GOVERNANCE_DECISION:
        for v in eff.verification_log:
            if v.decision_record_id == rec_id:
                authority_class = v.authority_class.value
                lifecycle = "effective" if v.effective_for_policy else "ineffective"
                verified_repo = v.authority_class == AuthorityClass.VERIFIED_REPOSITORY_APPROVAL
                break
    elif kind == RecordKind.CONFLICT:
        cst = eff.conflict_states.get(rec_id)
        if cst is not None and cst.adjudicated:
            authority_class = cst.authority_class.value
            lifecycle = f"adjudicated:{cst.disposition}"
        else:
            lifecycle = "unadjudicated"

    normative_statement = body.get("statement")
    if normative_statement is None and kind == RecordKind.APPLICATION:
        pv = by_id.get(str(body.get("practice_version_id", "")))
        if pv is not None:
            normative_statement = pv.get("body", {}).get("statement")

    scope = body.get("scope") or body.get("proposed_scope") or body.get("applicability")

    return {
        "target_type": "record",
        "record_id": rec_id,
        "kind": kind,
        "schema_version": rec.get("schema_version"),
        "record_sha256": canonical_sha256(rec),
        "created_at": rec.get("created_at"),
        "actor": rec.get("actor"),
        "capture_mode": _detect_record_capture_mode(rec),
        "normative_statement": normative_statement,
        "scope": scope,
        "authority_class": authority_class,
        "lifecycle": lifecycle,
        "verified_repository_approval": verified_repo,
        "evidence": evidence_details,
        "lineage": {
            "candidate_id": body.get("candidate_id"),
            "previous_version_id": body.get("previous_version_id"),
            "practice_version_id": body.get("practice_version_id"),
            "target_record_id": body.get("target_record_id"),
            "governance_decisions": decisions_targeting,
            "applications": related_apps,
            "conflicts": related_conflicts,
        },
        "record": rec,
    }


def _cmd_inspect(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    try:
        records, _, _ = _load_records_for_repo(repo_root, args.records_file)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, StoreError) as exc:
        _emit_error(str(exc), as_json=args.json, code="INSPECT_LOAD_ERROR")
        return EXIT_INVALID

    target = args.target
    by_id = {str(r["record_id"]): r for r in records if isinstance(r, dict) and "record_id" in r}

    result: dict[str, Any]
    try:
        if target in by_id:
            result = _inspect_record(
                by_id[target],
                records,
                repo_root=repo_root,
                as_of=args.as_of,
                allow_local=args.allow_local,
            )
        else:
            raw_path, l_start, _ = parse_source_spec(target)
            cand_path = Path(raw_path)
            abs_cand = cand_path if cand_path.is_absolute() else (repo_root / cand_path)
            if l_start is None and abs_cand.is_file() and abs_cand.suffix == ".json":
                if not _is_within_root(repo_root, abs_cand) and not cand_path.is_absolute():
                    raise ValueError(f"Path {target!r} escapes repository root.")
                parsed = json.loads(abs_cand.read_text(encoding="utf-8"))
                if isinstance(parsed, dict) and "record_id" in parsed and "kind" in parsed:
                    all_recs = records if parsed["record_id"] in by_id else [*records, parsed]
                    result = _inspect_record(
                        parsed,
                        all_recs,
                        repo_root=repo_root,
                        as_of=args.as_of,
                        allow_local=args.allow_local,
                    )
                else:
                    result = _inspect_source_file(repo_root, target, records)
            else:
                result = _inspect_source_file(repo_root, target, records)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        _emit_error(str(exc), as_json=args.json, code="INSPECT_TARGET_ERROR")
        return EXIT_INVALID

    if args.json:
        _emit_json(result)
        return EXIT_OK

    if result["target_type"] == "source-document":
        print(f"Source Document: {result['source_path']} (lines {result['line_start']}-{result['line_end']})")
        print(f"URI:             {result['source_uri']}")
        print(f"Authority class: {result['authority_class']} (inert — NOT an active practice)")
        print(f"Owner/Author:    {result['owner']}")
        print(f"File SHA-256:    {result['file_sha256']}")
        print(f"Excerpt SHA-256: {result['excerpt_sha256']}")
        print(f"Linked evidence: {result['linked_evidence_ids'] or 'none'}")
        print("\nPer-line source provenance:")
        for lp in result["lines"][:20]:
            print(f"  L{lp['line_number']:>4} [{lp['line_sha256'][:10]}] | {lp['text']}")
        if len(result["lines"]) > 20:
            print(f"  ... ({len(result['lines']) - 20} more line(s); use --json for full list)")
        if result["heuristic_statements"]:
            print(f"\nHeuristic scan statements ({len(result['heuristic_statements'])}, capture_mode=heuristic-scan):")
            for s in result["heuristic_statements"][:8]:
                print(f"  - L{s['line_number']} [{s['matched_signal']}]: {s['text']}")
        if result["linked_candidates"]:
            print(f"\nLinked candidates ({len(result['linked_candidates'])}):")
            for c in result["linked_candidates"]:
                print(f"  - {c['record_id']} (capture_mode={c['capture_mode']}, {c['authority_class']}): {c['statement']}")
        print(f"\nNote: {result['explanation']}")
    else:
        print(f"Record:          {result['record_id']} (kind={result['kind']}, schema={result['schema_version']})")
        print(f"Canonical SHA:   {result['record_sha256']}")
        print(f"Capture mode:    {result['capture_mode']}")
        print(f"Authority class: {result['authority_class']}")
        print(f"Lifecycle:       {result['lifecycle']} (verified_repo_approval={result['verified_repository_approval']})")
        if result.get("normative_statement"):
            print(f"Statement:       {result['normative_statement']}")
        if result.get("scope"):
            print(f"Scope:           {json.dumps(result['scope'], sort_keys=True)}")
        if result["evidence"]:
            print("\nEvidence & source provenance:")
            for ev in result["evidence"]:
                if ev.get("missing"):
                    print(f"  - {ev['record_id']} (MISSING)")
                else:
                    drift = ev.get("disk_verification", {}).get("status", "not-checked")
                    print(
                        f"  - {ev['record_id']}: {ev['source_uri']} "
                        f"(state={ev['source_state']}, capture_mode={ev['capture_mode']}, disk={drift})"
                    )
                    if ev.get("redacted_snapshot"):
                        print(f"    \"{ev['redacted_snapshot']}\"")
        decs = result["lineage"]["governance_decisions"]
        if decs:
            print("\nGovernance decisions:")
            for d in decs:
                print(
                    f"  - {d['record_id']}: action={d['action']} mode={d['authority_mode']} "
                    f"subject={d['subject']} verified_repo={d['verified_repository_approval']}"
                )
        if result["lineage"]["applications"]:
            print(f"\nApplications:    {result['lineage']['applications']}")
        if result["lineage"]["conflicts"]:
            print(f"Conflicts:       {result['lineage']['conflicts']}")

    return EXIT_OK


# ---------------------------------------------------------------------------
# Subcommand: propose
# ---------------------------------------------------------------------------
def _cmd_propose(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    if not repo_root.is_dir():
        _emit_error(f"Repository path not found: {repo_root}", as_json=args.json, code="PROPOSE_INPUT_ERROR")
        return EXIT_INVALID

    store = GitRecordStore(repo_root)
    try:
        result = propose_from_source(
            store,
            repo_root,
            statement=args.statement,
            evidence_specs=args.evidence,
            scope_repository=args.scope_repo or repo_root.name,
            scope_paths=args.scope_path or (),
            scope_languages=args.scope_lang or (),
            actor_id=args.actor,
            source_author=args.source_author,
            record_id=args.record_id,
            generator_name="onemind-cli-propose",
            generator_version=__version__,
            write=bool(args.write),
        )
    except (FileNotFoundError, ValueError, StoreError) as exc:
        code = "PATH_TRAVERSAL_REJECTED" if "escapes repository root" in str(exc) else "PROPOSE_INPUT_ERROR"
        _emit_error(str(exc), as_json=args.json, code=code)
        return EXIT_INVALID

    if not result.valid:
        if args.json:
            _emit_json(result.to_dict())
        else:
            sys.stderr.write("Proposal failed validation:\n")
            if result.validation:
                for issue in result.validation.errors:
                    sys.stderr.write(f"  - [{issue.code}] {issue.message}\n")
        return EXIT_INVALID

    cand_dict = result.to_dict()["candidate"]
    ev_dicts = result.to_dict()["evidence"]
    cand_id = str(cand_dict["record_id"])
    planned_files = [
        str(store.record_path(ev["kind"], ev["record_id"]).relative_to(repo_root))
        for ev in ev_dicts
    ] + [str(store.record_path(cand_dict["kind"], cand_id).relative_to(repo_root))]

    out_payload = result.to_dict()
    out_payload["planned_files"] = planned_files
    out_payload["dry_run"] = not bool(args.write)
    out_payload["authority_class"] = AuthorityClass.INERT_CANDIDATE.value
    out_payload["capture_mode"] = "explicit-capture"

    if args.json:
        _emit_json(out_payload)
        return EXIT_OK

    mode_label = "WRITTEN (inert records persisted atomically)" if args.write else "DRY-RUN PREVIEW (no files written)"
    print(f"OneMind propose — {mode_label}")
    print(f"Candidate ID:    {cand_id}")
    print("Capture mode:    explicit-capture")
    print(f"Authority class: {AuthorityClass.INERT_CANDIDATE.value} (never activated automatically)")
    print(f"Statement:       {args.statement}")
    print(f"Proposed scope:  {json.dumps(cand_dict['body']['proposed_scope'], sort_keys=True)}")
    print(f"Evidence IDs:    {cand_dict['body']['evidence_ids']}")
    print("\nTarget files:")
    for pf in planned_files:
        prefix = "+" if args.write else "~"
        print(f"  {prefix} {pf}")
    if not args.write:
        print("\nDry run complete. Re-run with --write to persist these inert records.")
    else:
        print("\nInert candidate written. A PracticeVersion and authorized GovernanceDecision are required to activate any practice.")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Subcommand: preview
# ---------------------------------------------------------------------------
def _cmd_preview(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    try:
        records, _, commit_ref = _load_records_for_repo(repo_root, args.records_file)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, StoreError) as exc:
        _emit_error(str(exc), as_json=args.json, code="PREVIEW_LOAD_ERROR")
        return EXIT_INVALID

    by_id = {str(r["record_id"]): r for r in records if isinstance(r, dict) and "record_id" in r}
    policy = load_policy(repo_root).with_local_assertions(bool(args.allow_local))
    eff = project_effective_state(records, policy=policy, commit_ref=commit_ref)

    app_rec: RecordDict | None = None
    pv_rec: RecordDict | None = None
    cand_rec: RecordDict | None = None

    if args.application:
        app_rec = by_id.get(args.application)
        if app_rec is None or app_rec.get("kind") != RecordKind.APPLICATION:
            _emit_error(
                f"Application record {args.application!r} not found.",
                as_json=args.json,
                code="PREVIEW_MISSING_APPLICATION",
            )
            return EXIT_INVALID
        pv_rec = by_id.get(str(app_rec["body"]["practice_version_id"]))
    elif args.practice:
        pv_rec = by_id.get(args.practice)
        if pv_rec is None or pv_rec.get("kind") != RecordKind.PRACTICE_VERSION:
            _emit_error(
                f"PracticeVersion record {args.practice!r} not found.",
                as_json=args.json,
                code="PREVIEW_MISSING_PRACTICE",
            )
            return EXIT_INVALID
        for r in records:
            if (
                r.get("kind") == RecordKind.APPLICATION
                and r.get("body", {}).get("practice_version_id") == args.practice
            ):
                app_rec = r
                break
    elif args.candidate:
        cand_rec = by_id.get(args.candidate)
        if cand_rec is None or cand_rec.get("kind") != RecordKind.PRACTICE_CANDIDATE:
            _emit_error(
                f"PracticeCandidate record {args.candidate!r} not found.",
                as_json=args.json,
                code="PREVIEW_MISSING_CANDIDATE",
            )
            return EXIT_INVALID
    else:
        apps = [r for r in records if r.get("kind") == RecordKind.APPLICATION]
        if len(apps) == 1:
            app_rec = apps[0]
            pv_rec = by_id.get(str(app_rec["body"]["practice_version_id"]))

    impl_id = args.checker
    if impl_id is None and app_rec is not None:
        impl_id = app_rec.get("body", {}).get("detection", {}).get("implementation")

    if not impl_id:
        _emit_error(
            "No checker implementation specified. Pass --application <id> or --checker builtin:api-compat-json.",
            as_json=args.json,
            code="PREVIEW_NO_CHECKER",
        )
        return EXIT_INVALID

    checker = resolve_checker(str(impl_id))

    base_input = args.base
    proposed_input = args.proposed
    if app_rec is not None:
        decl_inputs = app_rec.get("body", {}).get("detection", {}).get("inputs", [])
        if base_input is None and len(decl_inputs) >= 1:
            base_input = str(repo_root / decl_inputs[0])
        if proposed_input is None and len(decl_inputs) >= 2:
            proposed_input = str(repo_root / decl_inputs[1])

    for label, val in (("base", base_input), ("proposed", proposed_input)):
        if val is not None and not Path(val).is_absolute():
            candidate_p = repo_root / val
            if ".." in Path(val).parts or not _is_within_root(repo_root, candidate_p):
                _emit_error(
                    f"Input --{label} path {val!r} escapes repository root.",
                    as_json=args.json,
                    code="PATH_TRAVERSAL_REJECTED",
                )
                return EXIT_INVALID
            if label == "base":
                base_input = str(candidate_p)
            else:
                proposed_input = str(candidate_p)

    if pv_rec is not None:
        pv_state = eff.practice_states.get(str(pv_rec["record_id"]))
        practice_info = {
            "record_id": pv_rec["record_id"],
            "practice_id": pv_rec["body"]["practice_id"],
            "version": pv_rec["body"]["version"],
            "statement": pv_rec["body"]["statement"],
            "scope": pv_rec["body"]["scope"],
            "authority_class": pv_state.authority_class.value if pv_state else AuthorityClass.UNAPPROVED.value,
            "lifecycle": pv_state.lifecycle if pv_state else "unapproved",
            "verified_repository_approval": pv_state.verified_repository_approval if pv_state else False,
        }
    elif cand_rec is not None:
        practice_info = {
            "record_id": cand_rec["record_id"],
            "kind": "practice_candidate",
            "statement": cand_rec["body"]["statement"],
            "scope": cand_rec["body"]["proposed_scope"],
            "authority_class": AuthorityClass.INERT_CANDIDATE.value,
            "lifecycle": "inert",
            "verified_repository_approval": False,
        }
    else:
        practice_info = {
            "record_id": None,
            "statement": "Standalone advisory checker preview (not bound to an approved practice).",
            "authority_class": AuthorityClass.UNAPPROVED.value,
            "lifecycle": "unapproved",
            "verified_repository_approval": False,
        }

    app_info: dict[str, Any] | None = None
    if app_rec is not None:
        app_state = eff.application_states.get(str(app_rec["record_id"]))
        app_info = {
            "record_id": app_rec["record_id"],
            "application_id": app_rec["body"]["application_id"],
            "application_version": app_rec["body"]["application_version"],
            "route": app_rec["body"]["route"],
            "implementation": app_rec["body"].get("detection", {}).get("implementation"),
            "authority_class": app_state.authority_class.value if app_state else AuthorityClass.UNAPPROVED.value,
            "lifecycle": app_state.lifecycle if app_state else "unapproved",
            "verified_repository_approval": app_state.verified_repository_approval if app_state else False,
        }

    if checker is None:
        payload = {
            "mode": "advisory-preview",
            "status": "indeterminate",
            "summary": (
                f"Unknown or untrusted checker implementation {impl_id!r}. "
                "OneMind refuses to execute arbitrary code referenced by records. "
                f"Registered built-in checkers: {[c['checker_id'] for c in list_builtin_checkers()]}."
            ),
            "practice": practice_info,
            "application": app_info,
            "check_result": None,
        }
        if args.json:
            _emit_json(payload)
        else:
            sys.stderr.write(f"INDETERMINATE: {payload['summary']}\n")
        return EXIT_INDETERMINATE

    check_res = checker.evaluate({"base": base_input, "proposed": proposed_input})
    payload = {
        "mode": "advisory-preview",
        "status": check_res.status,
        "practice": practice_info,
        "application": app_info,
        "check_result": check_res.to_dict(),
        "advisory_notice": (
            "Preview runs in local advisory mode only; it never mutates records, "
            "activates practices, enforces enterprise CI gates, or proves engineering improvement."
        ),
    }

    if args.json:
        _emit_json(payload)
    else:
        print("OneMind preview (advisory, offline, read-only)")
        print(f"Practice:        {practice_info.get('statement')}")
        print(
            f"Authority:       {practice_info.get('authority_class')} "
            f"(lifecycle={practice_info.get('lifecycle')}, "
            f"verified_repo_approval={practice_info.get('verified_repository_approval')})"
        )
        if app_info:
            print(
                f"Application:     {app_info['application_id']} v{app_info['application_version']} "
                f"(record={app_info['record_id']}, route={app_info['route']}, authority={app_info['authority_class']})"
            )
        print(f"Checker:         {check_res.checker_id} v{check_res.checker_version}")
        print(f"Inputs:          {', '.join(check_res.inputs_evaluated)}")
        print(f"Result:          {check_res.status.upper()} — {check_res.summary}")

        if check_res.findings:
            print("\nFindings:")
            for f in check_res.findings:
                loc = f" at {f.location}" if f.location else ""
                print(f"  - [{f.code}]{loc}: {f.message}")

        print("\nWhat this checker CAN detect:")
        for cap in check_res.capabilities:
            print(f"  + {cap}")

        print("\nWhat this result does NOT establish (known limitations):")
        for lim in check_res.known_limitations:
            print(f"  - {lim}")

        print(f"\nNote: {payload['advisory_notice']}")

    if check_res.status == "pass":
        return EXIT_OK
    if check_res.status == "fail":
        return EXIT_FINDING
    return EXIT_INDETERMINATE


# ---------------------------------------------------------------------------
# Subcommand: validate
# ---------------------------------------------------------------------------
def _cmd_validate(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    try:
        records, load_issues, _ = _load_records_for_repo(repo_root, args.records_file)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, StoreError) as exc:
        _emit_error(str(exc), as_json=args.json, code="VALIDATE_LOAD_ERROR")
        return EXIT_INVALID

    report = validate_records(records)
    report.issues = [*load_issues, *report.issues]

    if args.records_file is not None and args.trusted_base is None:
        report.historical_verification = {
            "status": "unavailable",
            "reason": "Validating standalone records file; no Git trusted base provided.",
        }
    else:
        store = GitRecordStore(repo_root)
        tb_report = store.verify_against_trusted_base(args.trusted_base)
        report.historical_verification = tb_report.to_dict()
        if tb_report.issues:
            report.issues.extend(tb_report.issues)

    report.valid = not any(i.severity == "error" for i in report.issues)

    if args.json:
        _emit_json(report.to_dict())
    else:
        status_word = "VALID" if report.valid else "INVALID"
        print(f"OneMind validate ({SCHEMA_VERSION} {PROTOCOL_STATUS}) — {status_word}")
        print(f"Records checked:         {report.record_count}")
        if report.counts_by_kind:
            breakdown = ", ".join(f"{k}={v}" for k, v in sorted(report.counts_by_kind.items()))
            print(f"By kind:                 {breakdown}")
        hv = report.historical_verification
        hv_status = hv.get("status", "unavailable")
        hv_detail = hv.get("resolved_commit") or hv.get("reason") or ""
        print(f"Historical verification: {hv_status} ({hv_detail})")

        if report.errors:
            print(f"\nErrors ({len(report.errors)}):")
            for err in report.errors:
                rid = f" [{err.record_id}]" if err.record_id else ""
                print(f"  x [{err.code}]{rid}: {err.message}")

        if report.warnings:
            print(f"\nWarnings ({len(report.warnings)}):")
            for warn in report.warnings:
                rid = f" [{warn.record_id}]" if warn.record_id else ""
                print(f"  ! [{warn.code}]{rid}: {warn.message}")

    return EXIT_OK if report.valid else EXIT_FINDING


# ---------------------------------------------------------------------------
# Parser & Entrypoint
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="onemind",
        description=(
            f"OneMind CLI v{__version__} (protocol {SCHEMA_VERSION}, {PROTOCOL_STATUS}) — "
            "offline, local-first engineering-intelligence substrate."
        ),
    )
    parser.add_argument("--version", action="version", version=f"onemind {__version__} ({SCHEMA_VERSION})")
    sub = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = sub.add_parser(
        "scan",
        help="Offline, read-only scan of instruction files and ADRs with per-line provenance.",
    )
    p_scan.add_argument("repo", nargs="?", default=".", help="Path to repository directory (default: .)")
    p_scan.add_argument(
        "--include",
        action="append",
        default=[],
        help="Additional repo-relative file or directory path to include (repeatable).",
    )
    p_scan.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_FILE_BYTES,
        help=f"Maximum bytes per scanned file (default: {DEFAULT_MAX_FILE_BYTES}).",
    )
    p_scan.add_argument("--json", action="store_true", help="Emit machine-readable JSON to stdout.")
    p_scan.set_defaults(func=_cmd_scan)

    # inspect
    p_inspect = sub.add_parser(
        "inspect",
        help="Explain per-line provenance, capture mode, authority class, lineage, and scope.",
    )
    p_inspect.add_argument(
        "target",
        help="Record ID, path to a record JSON file, or repo source file (optionally path:line[-end]).",
    )
    p_inspect.add_argument("--repo", default=".", help="Path to repository directory (default: .)")
    p_inspect.add_argument("--records-file", default=None, help="Optional JSON file containing records array.")
    p_inspect.add_argument("--as-of", default=None, help="RFC 3339 timestamp for deterministic projection.")
    p_inspect.add_argument(
        "--allow-local",
        action="store_true",
        help="Include local-owner-assertion decisions when projecting authority state.",
    )
    p_inspect.add_argument("--json", action="store_true", help="Emit machine-readable JSON to stdout.")
    p_inspect.set_defaults(func=_cmd_inspect)

    # propose
    p_propose = sub.add_parser(
        "propose",
        help="Explicitly capture an inert PracticeCandidate from source evidence (dry-run by default).",
    )
    p_propose.add_argument("--repo", default=".", help="Path to repository directory (default: .)")
    p_propose.add_argument("--statement", required=True, help="Normative practice statement to propose.")
    p_propose.add_argument(
        "--evidence",
        action="append",
        required=True,
        help="Existing evidence record ID or repo source file[:line[-end]] (repeatable).",
    )
    p_propose.add_argument("--scope-repo", default=None, help="Repository identifier for proposed_scope.")
    p_propose.add_argument(
        "--scope-path",
        action="append",
        default=[],
        help="Path glob for proposed_scope (repeatable, e.g. 'api/**').",
    )
    p_propose.add_argument(
        "--scope-lang",
        action="append",
        default=[],
        help="Language for proposed_scope (repeatable, e.g. 'python').",
    )
    p_propose.add_argument("--actor", default="local-developer", help="Human actor ID (default: local-developer).")
    p_propose.add_argument("--source-author", default=None, help="Optional source author attribution.")
    p_propose.add_argument("--record-id", default=None, help="Optional explicit candidate record_id.")
    p_propose.add_argument(
        "--write",
        action="store_true",
        help="Atomically persist inert record(s) under .onemind/records/. Without --write, runs as dry-run preview.",
    )
    p_propose.add_argument("--json", action="store_true", help="Emit machine-readable JSON to stdout.")
    p_propose.set_defaults(func=_cmd_propose)

    # preview
    p_preview = sub.add_parser(
        "preview",
        help="Run a deterministic local checker in advisory mode with full explainability.",
    )
    p_preview.add_argument("--repo", default=".", help="Path to repository directory (default: .)")
    p_preview.add_argument("--records-file", default=None, help="Optional JSON file containing records array.")
    p_preview.add_argument("--application", default=None, help="Application record ID to preview.")
    p_preview.add_argument("--practice", default=None, help="PracticeVersion record ID to preview.")
    p_preview.add_argument("--candidate", default=None, help="PracticeCandidate record ID to preview.")
    p_preview.add_argument(
        "--checker",
        default=None,
        help="Registered built-in checker ID (e.g. 'builtin:api-compat-json').",
    )
    p_preview.add_argument("--base", default=None, help="Baseline schema JSON file path.")
    p_preview.add_argument("--proposed", default=None, help="Proposed schema JSON file path.")
    p_preview.add_argument(
        "--allow-local",
        action="store_true",
        help="Show local-owner-assertion authority state (still advisory only).",
    )
    p_preview.add_argument(
        "--explain",
        action="store_true",
        help="Include full explanation of practice, capabilities, and limitations (always shown in detail).",
    )
    p_preview.add_argument("--json", action="store_true", help="Emit machine-readable JSON to stdout.")
    p_preview.set_defaults(func=_cmd_preview)

    # validate
    p_validate = sub.add_parser(
        "validate",
        help="Validate schema, references, governance invariants, and optional trusted-base immutability.",
    )
    p_validate.add_argument("repo", nargs="?", default=".", help="Path to repository directory (default: .)")
    p_validate.add_argument("--records-file", default=None, help="Validate a standalone JSON records file.")
    p_validate.add_argument(
        "--trusted-base",
        default=None,
        help="Separately established Git commit/ref for immutable historical mutation verification.",
    )
    p_validate.add_argument("--json", action="store_true", help="Emit machine-readable JSON to stdout.")
    p_validate.set_defaults(func=_cmd_validate)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = args.func
    return int(func(args))


if __name__ == "__main__":
    raise SystemExit(main())
