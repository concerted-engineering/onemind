"""End-to-end tests for examples/api-compat-demo and scripts/demo.sh."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from onemind.checks.api_compat import ApiCompatJsonChecker
from onemind.cli import EXIT_FINDING, EXIT_INDETERMINATE, EXIT_OK, main
from onemind.protocol.validator import validate_records
from onemind.storage.git_store import GitRecordStore


def _demo_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "examples" / "api-compat-demo"


def test_demo_store_records_are_valid() -> None:
    store = GitRecordStore(_demo_dir())
    records = store.list_records()
    assert len(records) == 7
    report = validate_records(records)
    assert report.valid, [i.to_dict() for i in report.issues]


def test_api_compat_checker_real_failure_and_success() -> None:
    demo = _demo_dir()
    checker = ApiCompatJsonChecker()

    fail_res = checker.evaluate(
        {
            "base": demo / "api/schemas/orders_v1_baseline.json",
            "proposed": demo / "api/schemas/orders_v1_breaking.json",
        }
    )
    assert fail_res.status == "fail"
    codes = {f.code for f in fail_res.findings}
    assert "API_FIELD_REMOVED" in codes
    assert "API_FIELD_TYPE_CHANGED" in codes
    assert "API_REQUIRED_FIELD_DROPPED" in codes

    pass_res = checker.evaluate(
        {
            "base": demo / "api/schemas/orders_v1_baseline.json",
            "proposed": demo / "api/schemas/orders_v1_compatible.json",
        }
    )
    assert pass_res.status == "pass"
    assert pass_res.findings == []


def test_api_compat_checker_missing_or_malformed_is_indeterminate(tmp_path: Path) -> None:
    demo = _demo_dir()
    checker = ApiCompatJsonChecker()

    # Missing file -> indeterminate, NEVER pass
    res_missing = checker.evaluate(
        {
            "base": demo / "api/schemas/orders_v1_baseline.json",
            "proposed": tmp_path / "does_not_exist.json",
        }
    )
    assert res_missing.status == "indeterminate"

    # Malformed JSON -> indeterminate
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    res_bad = checker.evaluate(
        {
            "base": demo / "api/schemas/orders_v1_baseline.json",
            "proposed": bad,
        }
    )
    assert res_bad.status == "indeterminate"


def test_cli_preview_on_demo_breaking_and_compatible(capsys) -> None:
    demo = str(_demo_dir())
    rc_fail = main(
        [
            "preview",
            "--repo",
            demo,
            "--application",
            "demo-app-0001",
            "--base",
            "api/schemas/orders_v1_baseline.json",
            "--proposed",
            "api/schemas/orders_v1_breaking.json",
            "--allow-local",
            "--json",
        ]
    )
    assert rc_fail == EXIT_FINDING
    payload_fail = json.loads(capsys.readouterr().out)
    assert payload_fail["status"] == "fail"
    assert payload_fail["practice"]["verified_repository_approval"] is False
    assert payload_fail["practice"]["authority_class"] == "local-owner-assertion"

    rc_pass = main(
        [
            "preview",
            "--repo",
            demo,
            "--application",
            "demo-app-0001",
            "--base",
            "api/schemas/orders_v1_baseline.json",
            "--proposed",
            "api/schemas/orders_v1_compatible.json",
            "--allow-local",
            "--json",
        ]
    )
    assert rc_pass == EXIT_OK
    payload_pass = json.loads(capsys.readouterr().out)
    assert payload_pass["status"] == "pass"

    # Unknown checker returns EXIT_INDETERMINATE (3)
    rc_ind = main(
        [
            "preview",
            "--repo",
            demo,
            "--checker",
            "untrusted:custom_script.py",
            "--json",
        ]
    )
    assert rc_ind == EXIT_INDETERMINATE


def test_demo_shell_script_runs_offline_and_cleanly() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "demo.sh"
    assert script.is_file()
    proc = subprocess.run([str(script)], cwd=root, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"demo.sh failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "Breaking change check exit code: 1" in proc.stdout
    assert "Result:          PASS" in proc.stdout
    assert "VALID" in proc.stdout
