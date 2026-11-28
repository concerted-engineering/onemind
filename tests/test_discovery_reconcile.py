"""Gate B (M2B) acceptance tests: discovery, independent grouping, reconciliation, digest, and evaluation."""

from __future__ import annotations

import copy
import json
import random
import socket
from pathlib import Path
from typing import Any

import pytest

from onemind import ResolutionContext, resolve
from onemind.cli import EXIT_INDETERMINATE, EXIT_OK, main
from onemind.discovery import (
    DeterministicReviewExtractor,
    OptionalModelExtractor,
    discover_recurring_corrections,
    evaluate_corpus_directory,
    reconcile_candidates,
    render_review_digest_markdown,
)
from onemind.governance.policy import AuthorityPolicy
from onemind.protocol.canonical import canonical_sha256
from onemind.protocol.models import RecordKind, ResolutionState
from onemind.protocol.validator import validate_records
from onemind.storage.git_store import GitRecordStore


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Network access is strictly forbidden in M2B offline tests!")

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_gold_corpus() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    gold = _repo_root() / "examples" / "discovery-gold-corpus"
    records = json.loads((gold / "records.json").read_text(encoding="utf-8"))
    runs = json.loads((gold / "ingestion_runs.json").read_text(encoding="utf-8"))
    return records, runs


def _seed_store_from_corpus(tmp_path: Path, corpus_name: str) -> Path:
    repo_dir = tmp_path / corpus_name
    repo_dir.mkdir()
    src_dir = _repo_root() / "examples" / corpus_name
    records = json.loads((src_dir / "records.json").read_text(encoding="utf-8"))
    store = GitRecordStore(repo_dir)
    store.append_many(records)
    runs_file = src_dir / "ingestion_runs.json"
    if runs_file.is_file():
        for run in json.loads(runs_file.read_text(encoding="utf-8")):
            store.append_ingestion_run(run)
    return repo_dir


# ---------------------------------------------------------------------------
# Gate B.1 & B.6: Gold & independent held-out corpus multi-axis evaluation
# ---------------------------------------------------------------------------
def test_gold_and_heldout_synthetic_corpus_evaluations() -> None:
    gold_eval = evaluate_corpus_directory(_repo_root() / "examples" / "discovery-gold-corpus")
    assert gold_eval.detection.precision == 1.0
    assert gold_eval.detection.recall == 1.0
    assert gold_eval.clustering.precision == 1.0
    assert gold_eval.clustering.recall == 1.0
    assert gold_eval.evidence_linking.precision == 1.0
    assert gold_eval.evidence_linking.recall == 1.0
    assert gold_eval.scope_correctness.precision == 1.0
    assert gold_eval.scope_correctness.recall == 1.0
    assert gold_eval.contradictions_detected >= 1
    assert gold_eval.corpus_complete is False  # Incomplete ingestion run preserved!
    assert "Synthetic offline fixture benchmark only" in gold_eval.disclaimer

    held_eval = evaluate_corpus_directory(_repo_root() / "examples" / "discovery-heldout-corpus")
    assert held_eval.detection.precision == 1.0
    assert held_eval.detection.recall == 1.0
    assert held_eval.clustering.precision == 1.0
    assert held_eval.clustering.recall == 1.0
    assert held_eval.evidence_linking.precision == 1.0
    assert held_eval.evidence_linking.recall == 1.0
    assert held_eval.scope_correctness.precision == 1.0
    assert held_eval.scope_correctness.recall == 1.0
    assert held_eval.corpus_complete is True


# ---------------------------------------------------------------------------
# Gate B.2: Reproducibility across reruns and shuffled input order
# ---------------------------------------------------------------------------
def test_discovery_and_reconciliation_deterministic_under_shuffled_input() -> None:
    records, runs = _load_gold_corpus()
    ev_records = [r for r in records if r["kind"] == RecordKind.EVIDENCE]

    r1 = discover_recurring_corrections(
        ev_records,
        repository="synthetic/payments-core",
        min_independent_prs=2,
        ingestion_runs=runs,
        created_at="2026-11-28T00:00:00Z",
    )

    shuffled = list(ev_records)
    rng = random.Random(42)
    rng.shuffle(shuffled)
    r2 = discover_recurring_corrections(
        shuffled,
        repository="synthetic/payments-core",
        min_independent_prs=2,
        ingestion_runs=runs,
        created_at="2026-11-28T00:00:00Z",
    )

    assert r1.to_dict() == r2.to_dict()

    rec1 = reconcile_candidates(r1.proposed_candidates, records, created_at="2026-11-28T00:00:00Z")
    rec2 = reconcile_candidates(
        list(reversed(r2.proposed_candidates)),
        list(reversed(records)),
        created_at="2026-11-28T00:00:00Z",
    )
    assert rec1.to_dict() == rec2.to_dict()


# ---------------------------------------------------------------------------
# Gate B.3 & B.4: Independent PR counting, superseded edits, no contradiction merge
# ---------------------------------------------------------------------------
def test_independent_pr_counting_and_non_merger_of_contradictions() -> None:
    records, runs = _load_gold_corpus()
    ev_records = [r for r in records if r["kind"] == RecordKind.EVIDENCE]

    report = discover_recurring_corrections(
        ev_records,
        repository="synthetic/payments-core",
        min_independent_prs=2,
        ingestion_runs=runs,
    )

    # Superseded edit revision excluded so PR #102 is not double-counted
    assert "gold-ev-api-2-old" in report.extraction.superseded_record_ids

    # API compat cluster: 3 comments (2 in PR #101 + 1 edited in PR #102) -> independent_pr_count == 2!
    api_cluster = next(
        c for c in report.clusters if "gold-ev-api-1a" in c.evidence_ids
    )
    assert api_cluster.support.total_source_comments == 3
    assert api_cluster.support.independent_pr_count == 2
    assert api_cluster.support.distinct_prs == (101, 102)
    assert api_cluster.meets_min_support is True

    # Single-PR migration comment stays in insufficient_support_clusters (NOT promoted!)
    assert len(report.insufficient_support_clusters) == 1
    assert report.insufficient_support_clusters[0].support.independent_pr_count == 1
    assert report.insufficient_support_clusters[0].evidence_ids == ("gold-ev-single-pr",)

    # Contradictory caching clusters MUST NOT merge and MUST cross-reference each other
    cache_clusters = [c for c in report.clusters if "cache" in c.topic_key]
    assert len(cache_clusters) == 2
    assert {c.polarity for c in cache_clusters} == {"require", "forbid"}
    assert len(report.contradictory_cluster_pairs) == 1
    assert all(c.contradicts_cluster_ids for c in cache_clusters)
    assert all(c.needs_manual_partitioning for c in cache_clusters)

    # Filtered noise check: style, prompt injection, bot, compliment, withdrawn, unavailable, question
    filtered_by_id = {f.record_id: f.reason for f in report.extraction.filtered}
    assert filtered_by_id["gold-ev-style-1"] == "style-preference-not-promoted"
    assert filtered_by_id["gold-ev-style-2"] == "style-preference-not-promoted"
    assert filtered_by_id["gold-ev-injection"] == "prompt-injection-filtered"
    assert filtered_by_id["gold-ev-bot"] == "bot-chatter-filtered"
    assert filtered_by_id["gold-ev-lgtm"] == "compliment-or-boilerplate-filtered"
    assert filtered_by_id["gold-ev-withdrawn"] == "withdrawn-comment-filtered"
    assert filtered_by_id["gold-ev-deleted"] == "source-unavailable-excluded"
    assert filtered_by_id["gold-ev-question"] == "routine-question-filtered"


# ---------------------------------------------------------------------------
# Gate B.5 & B.8: Incomplete ingestion & metadata-only privacy propagate explicitly
# ---------------------------------------------------------------------------
def test_incomplete_ingestion_and_metadata_only_propagate_incomplete_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 1. Metadata-only GitHub ingestion -> discover reports missing_redacted_text and exits 3!
    repo_meta = tmp_path / "meta_repo"
    repo_meta.mkdir()
    fixture = _repo_root() / "examples" / "github-ingest-fixture" / "github_complete.json"
    assert (
        main(
            [
                "ingest",
                "github",
                "--repository",
                "example/service-api",
                "--store",
                str(repo_meta),
                "--since",
                "2026-10-01",
                "--until",
                "2026-11-01",
                "--capture-policy",
                "metadata-only",
                "--fixture-file",
                str(fixture),
                "--write",
                "--json",
            ]
        )
        == EXIT_OK
    )
    capsys.readouterr()

    rc_disc = main(["discover", "--repo", str(repo_meta), "--source", "github", "--json"])
    assert rc_disc == EXIT_INDETERMINATE
    disc_data = json.loads(capsys.readouterr().out)
    assert disc_data["corpus_complete"] is False
    assert "missing_redacted_text" in disc_data["incomplete_reasons"]
    assert disc_data["extraction_summary"]["missing_redacted_text_count"] == 5
    assert disc_data["promoted_candidate_count"] == 0


# ---------------------------------------------------------------------------
# Gate B.6 & B.7: Non-interference invariant & zero PracticeVersion/Decision/App creation
# ---------------------------------------------------------------------------
def test_non_interference_existing_approved_practice_unchanged_after_discover_reconcile_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_dir = _seed_store_from_corpus(tmp_path, "discovery-gold-corpus")
    store = GitRecordStore(repo_dir)
    policy = AuthorityPolicy(authorized_reviewers=frozenset({"lead-maintainer"}))
    ctx = ResolutionContext(repository="synthetic/payments-core", paths=("api/schemas/orders_v1.json",))

    # Capture byte-for-byte existing PracticeVersion + GovernanceDecision and effective state BEFORE M2B
    pv_path = store.record_path("practice_version", "gold-pv-approved-api-compat")
    dec_path = store.record_path("governance_decision", "gold-dec-approved-api-compat")
    pv_bytes_before = pv_path.read_bytes()
    dec_bytes_before = dec_path.read_bytes()
    res_before = resolve(store, ctx, as_of="2026-11-28T00:00:00Z", policy=policy)
    assert res_before.state == ResolutionState.ACTIVE
    assert [p.record_id for p in res_before.practices] == ["gold-pv-approved-api-compat"]

    # Run discover --output (standalone) + --write (persists inert candidates ONLY)
    proposals_file = tmp_path / "proposals.json"
    rc_disc = main(
        [
            "discover",
            "--repo",
            str(repo_dir),
            "--source",
            "github",
            "--min-independent-prs",
            "2",
            "--output",
            str(proposals_file),
            "--write",
            "--json",
        ]
    )
    # Exits 3 because gold corpus contains an incomplete ingestion run!
    assert rc_disc == EXIT_INDETERMINATE
    disc_out = json.loads(capsys.readouterr().out)
    assert disc_out["written"] is True
    assert proposals_file.is_file()
    assert disc_out["promoted_candidate_count"] == 4

    # Run reconcile --candidates-file --write (persists inert Conflict records ONLY)
    rc_rec = main(
        [
            "reconcile",
            "--repo",
            str(repo_dir),
            "--candidates-file",
            str(proposals_file),
            "--write",
            "--json",
        ]
    )
    assert rc_rec == EXIT_OK
    rec_out = json.loads(capsys.readouterr().out)
    assert rec_out["written"] is True
    assert rec_out["counts_by_classification"]["contradictory"] >= 1
    assert rec_out["proposed_conflict_count"] >= 1

    # Run review-digest --output (standalone Markdown)
    digest_file = tmp_path / "onemind-review.md"
    rc_dig = main(
        [
            "review-digest",
            "--repo",
            str(repo_dir),
            "--candidates-file",
            str(proposals_file),
            "--output",
            str(digest_file),
        ]
    )
    assert rc_dig == EXIT_INDETERMINATE
    dig_stdout = capsys.readouterr().out
    assert "No canonical .onemind/ records modified" in dig_stdout
    digest_md = digest_file.read_text(encoding="utf-8")
    assert "NON-AUTHORITATIVE REVIEW DIGEST" in digest_md
    assert "Extraction & Corpus Coverage" in digest_md
    assert "Reconciliation & Conflicts Against Existing Records" in digest_md
    assert "Suggested Human Reviewer Decisions" in digest_md

    # Verify ZERO new PracticeVersion, GovernanceDecision, Application, or OutcomeEvent created
    all_after = store.list_records()
    assert len([r for r in all_after if r["kind"] == RecordKind.PRACTICE_VERSION]) == 1
    assert len([r for r in all_after if r["kind"] == RecordKind.GOVERNANCE_DECISION]) == 1
    assert len([r for r in all_after if r["kind"] == RecordKind.APPLICATION]) == 0
    assert len([r for r in all_after if r["kind"] == RecordKind.OUTCOME_EVENT]) == 0

    # Verify existing approved practice is BYTE-FOR-BYTE and SEMANTICALLY UNCHANGED!
    assert pv_path.read_bytes() == pv_bytes_before
    assert dec_path.read_bytes() == dec_bytes_before
    res_after = resolve(store, ctx, as_of="2026-11-28T00:00:00Z", policy=policy)
    assert res_after.to_dict() == res_before.to_dict()

    # Entire store validates cleanly against 0.1-draft schema and semantic invariants!
    val = validate_records(all_after)
    assert val.valid, [i.to_dict() for i in val.issues]


# ---------------------------------------------------------------------------
# End-to-end offline M2A -> M2B CLI pipeline (read-only defaults verified)
# ---------------------------------------------------------------------------
def test_e2e_offline_ingest_to_discover_reconcile_digest_read_only_defaults(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "e2e_repo"
    repo.mkdir()
    fixture = _repo_root() / "examples" / "github-ingest-fixture" / "github_complete.json"

    # 1. Ingest with explicit redacted-excerpt opt-in
    assert (
        main(
            [
                "ingest",
                "github",
                "--repository",
                "example/service-api",
                "--store",
                str(repo),
                "--since",
                "2026-10-01",
                "--until",
                "2026-11-01",
                "--capture-policy",
                "redacted-excerpt",
                "--fixture-file",
                str(fixture),
                "--write",
                "--json",
            ]
        )
        == EXIT_OK
    )
    capsys.readouterr()

    store = GitRecordStore(repo)
    count_before = len(store.list_records())

    # 2. Read-only discover, reconcile, and review-digest MUST NOT modify canonical store!
    assert main(["discover", "--repo", str(repo), "--source", "github", "--min-independent-prs", "1", "--json"]) == EXIT_OK
    disc = json.loads(capsys.readouterr().out)
    assert disc["written"] is False
    assert disc["corpus_complete"] is True
    assert disc["promoted_candidate_count"] >= 2

    assert main(["reconcile", "--repo", str(repo), "--json"]) == EXIT_OK
    capsys.readouterr()

    assert main(["review-digest", "--repo", str(repo), "--min-independent-prs", "1"]) == EXIT_OK
    md = capsys.readouterr().out
    assert "# OneMind Proposal Review Digest" in md

    assert len(store.list_records()) == count_before


def test_reconciliation_covers_identical_duplicate_refinement_contradiction_and_insufficient() -> None:
    records, _ = _load_gold_corpus()
    existing_pv = next(r for r in records if r["record_id"] == "gold-pv-approved-api-compat")

    # 1. Identical candidate
    c_ident = {
        "schema_version": "0.1-draft",
        "record_id": "c-ident",
        "kind": "practice_candidate",
        "created_at": "2026-11-28T00:00:00Z",
        "actor": {"type": "service", "id": "test"},
        "body": {
            "statement": existing_pv["body"]["statement"],
            "proposed_scope": copy.deepcopy(existing_pv["body"]["scope"]),
            "evidence_ids": ["gold-ev-api-1a"],
            "generator": {"name": "test", "version": "0.1"},
        },
    }
    # 2. Compatible refinement (narrower path subset)
    c_refine = copy.deepcopy(c_ident)
    c_refine["record_id"] = "c-refine"
    c_refine["body"]["proposed_scope"]["paths"] = ["api/schemas/orders_v1.json"]

    # 3. Contradictory candidate (opposite polarity over same scope)
    c_contra = copy.deepcopy(c_ident)
    c_contra["record_id"] = "c-contra"
    c_contra["body"]["statement"] = "Always remove legacy response fields from public API schemas."

    # 4. Insufficient context (non-normative statement)
    c_insuff = copy.deepcopy(c_ident)
    c_insuff["record_id"] = "c-insuff"
    c_insuff["body"]["statement"] = "Notes about public API schemas."

    rep = reconcile_candidates([c_ident, c_refine, c_contra, c_insuff], [existing_pv])
    by_cid = {
        f.candidate_id: f.classification
        for f in rep.findings
        if f.existing_record_id == "gold-pv-approved-api-compat"
    }
    assert by_cid["c-ident"] == "identical"
    assert by_cid["c-refine"] == "compatible-refinement"
    assert by_cid["c-contra"] == "contradictory"
    assert by_cid["c-insuff"] == "insufficient-context"

    # Verify OptionalModelExtractor & DeterministicReviewExtractor protocol contracts
    ext = DeterministicReviewExtractor()
    assert ext.name == "onemind-deterministic-review-extractor"
    assert not isinstance(ext, OptionalModelExtractor)
    assert isinstance(canonical_sha256(rep.to_dict()), str)
    # Verify digest renders cleanly with empty/populated reconciliation
    disc_empty = discover_recurring_corrections([], repository="synthetic/empty")
    md_empty = render_review_digest_markdown(disc_empty, None)
    assert "No clusters met" in md_empty
