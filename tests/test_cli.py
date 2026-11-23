"""Integration tests for all five OneMind CLI commands and --json output contracts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from onemind.cli import EXIT_FINDING, EXIT_INVALID, EXIT_OK, main


def _init_temp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "sample_repo"
    repo.mkdir()
    (repo / "docs" / "adr").mkdir(parents=True)
    (repo / "docs" / "adr" / "0001-logging.md").write_text(
        "# ADR-0001: Structured Logging\n\n"
        "- **Owner:** platform-team\n\n"
        "Services MUST NOT log bearer tokens or raw secrets.\n",
        encoding="utf-8",
    )
    (repo / "AGENTS.md").write_text(
        "# Agent Notes\n\n"
        "Owner: platform-team\n\n"
        "- Always redact credentials before emitting logs.\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    return repo


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    snap: dict[str, bytes] = {}
    for p in sorted(root.rglob("*")):
        if ".git" in p.parts:
            continue
        if p.is_file():
            snap[p.relative_to(root).as_posix()] = p.read_bytes()
    return snap


def test_cli_scan_inspect_propose_validate_workflow(tmp_path: Path, capsys) -> None:
    repo = _init_temp_repo(tmp_path)
    before = _snapshot_tree(repo)

    # 1. scan --json (read-only)
    assert main(["scan", str(repo), "--json"]) == EXIT_OK
    scan_data = json.loads(capsys.readouterr().out)
    assert scan_data["available_source_count"] == 2
    assert scan_data["candidate_statement_count"] >= 2
    assert _snapshot_tree(repo) == before

    # 2. inspect source file --json (read-only)
    assert main(["inspect", "docs/adr/0001-logging.md:5", "--repo", str(repo), "--json"]) == EXIT_OK
    insp_src = json.loads(capsys.readouterr().out)
    assert insp_src["authority_class"] == "unverified-source-document"
    assert "MUST NOT log bearer tokens" in insp_src["excerpt"]
    assert _snapshot_tree(repo) == before

    # 3. propose dry-run (default: NO files written)
    assert (
        main(
            [
                "propose",
                "--repo",
                str(repo),
                "--statement",
                "Services must not log bearer tokens or raw secrets.",
                "--evidence",
                "docs/adr/0001-logging.md:5",
                "--scope-repo",
                "sample_repo",
                "--scope-path",
                "services/**",
                "--record-id",
                "cand-log-001",
                "--json",
            ]
        )
        == EXIT_OK
    )
    dry_data = json.loads(capsys.readouterr().out)
    assert dry_data["dry_run"] is True
    assert dry_data["written"] is False
    assert _snapshot_tree(repo) == before

    # 4. propose --write (writes inert evidence + candidate)
    assert (
        main(
            [
                "propose",
                "--repo",
                str(repo),
                "--statement",
                "Services must not log bearer tokens or raw secrets.",
                "--evidence",
                "docs/adr/0001-logging.md:5",
                "--scope-repo",
                "sample_repo",
                "--scope-path",
                "services/**",
                "--record-id",
                "cand-log-001",
                "--write",
                "--json",
            ]
        )
        == EXIT_OK
    )
    write_data = json.loads(capsys.readouterr().out)
    assert write_data["written"] is True
    assert "cand-log-001" in write_data["written_record_ids"]
    assert write_data["authority_class"] == "inert-candidate"

    # 5. inspect written candidate record
    assert main(["inspect", "cand-log-001", "--repo", str(repo), "--json"]) == EXIT_OK
    insp_rec = json.loads(capsys.readouterr().out)
    assert insp_rec["kind"] == "practice_candidate"
    assert insp_rec["authority_class"] == "inert-candidate"
    assert insp_rec["lifecycle"] == "inert"

    # Commit records, establish trusted base, then test validate
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add candidate"], cwd=repo, check=True, capture_output=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    # Validate without trusted-base reports unavailable
    assert main(["validate", str(repo), "--json"]) == EXIT_OK
    val_unavail = json.loads(capsys.readouterr().out)
    assert val_unavail["valid"] is True
    assert val_unavail["historical_verification"]["status"] == "unavailable"

    # Validate WITH trusted-base reports verified
    assert main(["validate", str(repo), "--trusted-base", base_sha, "--json"]) == EXIT_OK
    val_ok = json.loads(capsys.readouterr().out)
    assert val_ok["valid"] is True
    assert val_ok["historical_verification"]["status"] == "verified"

    # Tamper with immutable candidate on disk -> validate fails with EXIT_FINDING (1)
    cand_file = repo / ".onemind" / "records" / "practice_candidate" / "cand-log-001.json"
    tampered = json.loads(cand_file.read_text(encoding="utf-8"))
    tampered["body"]["statement"] = "Tampered statement"
    cand_file.write_text(json.dumps(tampered, indent=2) + "\n", encoding="utf-8")

    assert main(["validate", str(repo), "--trusted-base", base_sha, "--json"]) == EXIT_FINDING
    val_fail = json.loads(capsys.readouterr().out)
    assert val_fail["valid"] is False
    assert any(i["code"] == "IMMUTABLE_RECORD_MODIFIED" for i in val_fail["issues"])


def test_cli_invalid_arguments_return_exit_invalid(tmp_path: Path, capsys) -> None:
    rc = main(["scan", str(tmp_path / "does_not_exist"), "--json"])
    assert rc == EXIT_INVALID
    err = json.loads(capsys.readouterr().out)
    assert "error" in err


def test_real_file_source_linked_candidate_through_api_and_cli_with_per_line_provenance(
    tmp_path: Path, capsys
) -> None:
    """Demonstrate capturing a source-linked inert candidate from a real file via both API and CLI,
    recording per-line provenance, heuristic vs explicit-capture distinction, and disk drift detection.
    """
    from onemind import propose_from_source, query
    from onemind.storage.git_store import GitRecordStore

    repo = _init_temp_repo(tmp_path)

    # 1. Scan reports per-line provenance and explicit capture_mode legend
    assert main(["scan", str(repo), "--json"]) == EXIT_OK
    scan_out = json.loads(capsys.readouterr().out)
    assert "heuristic-scan" in scan_out["capture_mode_legend"]
    assert "explicit-capture" in scan_out["capture_mode_legend"]
    adr_stmts = [
        s for s in scan_out["candidate_statements"] if s["source_path"] == "docs/adr/0001-logging.md"
    ]
    assert len(adr_stmts) == 1
    assert adr_stmts[0]["line_number"] == 5
    assert adr_stmts[0]["source_uri"] == "repo://docs/adr/0001-logging.md#L5"
    assert adr_stmts[0]["capture_mode"] == "heuristic-scan"
    assert adr_stmts[0]["matched_signal"] == "normative-keyword:MUST NOT"

    # 2. Capture source-linked candidate from the REAL file via Python library API
    store = GitRecordStore(repo)
    api_prop = propose_from_source(
        store,
        repo,
        statement="Services must never serialize bearer tokens into structured logs.",
        evidence_specs=["docs/adr/0001-logging.md:5"],
        scope_repository="sample_repo",
        scope_paths=["services/**"],
        record_id="cand-api-logging-001",
        write=True,
    )
    assert api_prop.valid is True
    assert api_prop.written is True
    assert api_prop.candidate["body"]["generator"]["capture_mode"] == "explicit-capture"
    ev_ext = api_prop.evidence[0]["extensions"]["source_provenance"]
    assert ev_ext["capture_mode"] == "explicit-capture"
    assert ev_ext["lines"][0]["line_number"] == 5

    # 3. Capture a second candidate from the same real file via CLI (`onemind propose --write`)
    assert (
        main(
            [
                "propose",
                "--repo",
                str(repo),
                "--statement",
                "Always redact credentials before emitting logs.",
                "--evidence",
                "AGENTS.md:5",
                "--scope-repo",
                "sample_repo",
                "--record-id",
                "cand-cli-redact-001",
                "--write",
                "--json",
            ]
        )
        == EXIT_OK
    )
    cli_prop = json.loads(capsys.readouterr().out)
    assert cli_prop["written"] is True
    assert cli_prop["capture_mode"] == "explicit-capture"

    # Both candidates remain inert when queried through library API
    q = query(store, {"kinds": ["practice_candidate"]})
    assert {r.record.record_id for r in q.records} == {"cand-api-logging-001", "cand-cli-redact-001"}
    assert all(r.lifecycle == "inert" for r in q.records)

    # 4. Inspect candidate reports per-line provenance and live disk verification = matches-disk
    assert main(["inspect", "cand-api-logging-001", "--repo", str(repo), "--json"]) == EXIT_OK
    insp_ok = json.loads(capsys.readouterr().out)
    assert insp_ok["capture_mode"] == "explicit-capture"
    assert insp_ok["evidence"][0]["disk_verification"]["status"] == "matches-disk"

    # 5. Modify source file on disk -> inspect detects source-drifted cleanly!
    adr_path = repo / "docs" / "adr" / "0001-logging.md"
    adr_path.write_text(
        adr_path.read_text(encoding="utf-8").replace("bearer tokens", "OAuth tokens"),
        encoding="utf-8",
    )
    assert main(["inspect", "cand-api-logging-001", "--repo", str(repo), "--json"]) == EXIT_OK
    insp_drift = json.loads(capsys.readouterr().out)
    assert insp_drift["evidence"][0]["disk_verification"]["status"] == "source-drifted"
