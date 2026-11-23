"""Tests for generic workflow adapter composability across unrelated representations."""

from __future__ import annotations

import json
from pathlib import Path

from onemind.adapters.interfaces import Extractor, SourceAdapter
from onemind.adapters.json_observation import JsonObservationAdapter
from onemind.adapters.markdown_retro import MarkdownRetroAdapter
from onemind.adapters.reporter import SummaryReporter
from onemind.engine import OneMindEngine
from onemind.governance.policy import AuthorityPolicy
from onemind.protocol.models import AuthorityClass, ResolutionState
from onemind.protocol.validator import validate_records
from onemind.storage.base import InMemoryStore
from onemind.storage.git_store import GitRecordStore


def _fixtures_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "examples" / "workflow-fixtures"


def _run_common_pipeline(
    adapter: SourceAdapter,
    extractor: Extractor,
    fixture_path: Path,
    engine: OneMindEngine,
    *,
    repository: str,
) -> tuple[int, int]:
    """Ingest and propose through the shared protocol contracts without adapter-specific logic."""
    ingested = adapter.ingest(fixture_path, repository=repository)
    assert ingested.complete is True
    assert len(ingested.evidence_records) >= 1

    candidates = extractor.extract(ingested.evidence_records, repository=repository)
    assert len(candidates) == len(ingested.evidence_records)

    ev_by_id = {ev["record_id"]: ev for ev in ingested.evidence_records}
    for cand in candidates:
        linked_ev = [ev_by_id[eid] for eid in cand["body"]["evidence_ids"]]
        proposal = engine.propose(cand, evidence=linked_ev, write=True)
        assert proposal.valid is True
        assert proposal.written is True

    return len(ingested.evidence_records), len(candidates)


def test_two_unrelated_workflow_fixtures_share_common_adapter_contract(tmp_path: Path) -> None:
    store = GitRecordStore(tmp_path / "repo")
    engine = OneMindEngine(store, policy=AuthorityPolicy(allow_local_assertions=True))
    repo_name = "org/billing-platform"

    retro_adapter = MarkdownRetroAdapter()
    obs_adapter = JsonObservationAdapter()

    # Both satisfy the exact same SourceAdapter and Extractor protocols
    assert isinstance(retro_adapter, SourceAdapter)
    assert isinstance(retro_adapter, Extractor)
    assert isinstance(obs_adapter, SourceAdapter)
    assert isinstance(obs_adapter, Extractor)

    ev_count_1, cand_count_1 = _run_common_pipeline(
        retro_adapter,
        retro_adapter,
        _fixtures_dir() / "retro-sprint-42.md",
        engine,
        repository=repo_name,
    )
    ev_count_2, cand_count_2 = _run_common_pipeline(
        obs_adapter,
        obs_adapter,
        _fixtures_dir() / "ci-observations.json",
        engine,
        repository=repo_name,
    )

    assert ev_count_1 == 2 and cand_count_1 == 2
    assert ev_count_2 == 2 and cand_count_2 == 2

    # 1. All canonical records validate cleanly together
    all_records = store.list_records()
    report = validate_records(all_records)
    assert report.valid, [i.to_dict() for i in report.issues]

    # 2. Independent provenance is preserved
    q_retro = engine.query({"source_type": "markdown-retrospective"})
    q_obs = engine.query({"source_type": "json-observation"})
    assert len(q_retro.records) == 2
    assert len(q_obs.records) == 2
    assert all(r.authority_class == AuthorityClass.INERT_EVIDENCE for r in q_retro.records)
    assert all(r.authority_class == AuthorityClass.INERT_EVIDENCE for r in q_obs.records)

    # 3. Neither workflow fixture confers governance authority (even though ci-observations.json
    #    contains "approved": true and "auto_activate": true!)
    res = engine.resolve(
        {"repository": repo_name, "paths": ["services/billing/client.py"]},
        allow_local_assertions=True,
    )
    assert res.state == ResolutionState.NONE
    assert res.practices == ()

    # 4. Independent removability: reading canonical records from a fresh store/engine
    #    without referencing either adapter works cleanly and reproduces identical query/report results.
    fresh_store = GitRecordStore(tmp_path / "repo")
    fresh_engine = OneMindEngine(fresh_store)
    q_all_cands = fresh_engine.query({"kinds": ["practice_candidate"]})
    assert len(q_all_cands.records) == 4
    assert all(r.authority_class == AuthorityClass.INERT_CANDIDATE for r in q_all_cands.records)

    summary = SummaryReporter().render(fresh_store.list_records(), as_of="2026-11-22T00:00:00Z")
    assert summary["record_counts_by_kind"] == {"evidence": 4, "practice_candidate": 4}
    assert summary["practice_lifecycles"] == {}


def test_adapters_fail_safely_on_unsupported_schemas_and_ignore_authority_claims(tmp_path: Path) -> None:
    # Unknown JSON observation schema -> complete=False and explicit unsupported_reason
    bad_json = tmp_path / "unknown.json"
    bad_json.write_text(json.dumps({"observation_schema": "unknown/v99", "observations": []}), encoding="utf-8")

    obs_adapter = JsonObservationAdapter()
    res_bad = obs_adapter.ingest(bad_json, repository="org/demo")
    assert res_bad.complete is False
    assert res_bad.unsupported_reason == "unsupported-schema:unknown/v99"
    assert res_bad.evidence_records == []

    # Warning emitted when fixture tries to claim approved=true
    res_ok = obs_adapter.ingest(_fixtures_dir() / "ci-observations.json", repository="org/demo")
    assert any("non-authoritative approval claim" in w for w in res_ok.warnings)

    # MarkdownRetroAdapter rejects non-markdown file explicitly
    retro_adapter = MarkdownRetroAdapter()
    res_wrong_ext = retro_adapter.ingest(bad_json, repository="org/demo")
    assert res_wrong_ext.complete is False
    assert res_wrong_ext.unsupported_reason == "unsupported-format:.json"

    # InMemoryStore + OneMindEngine remains fully functional with zero workflow adapters
    standalone = OneMindEngine(InMemoryStore())
    assert standalone.resolve({"repository": "org/demo"}).state == ResolutionState.NONE
