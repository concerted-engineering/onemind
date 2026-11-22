"""Tests for governance invariants, authority separation, conflicts, and deterministic replay."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from onemind.engine import OneMindEngine
from onemind.governance.policy import AuthorityPolicy
from onemind.governance.projection import project_effective_state, resolve_context
from onemind.protocol.canonical import canonical_sha256
from onemind.protocol.models import AuthorityClass, ResolutionState
from onemind.storage.base import InMemoryStore


def _fixture_records() -> list[dict]:
    root = Path(__file__).resolve().parents[1]
    return json.loads((root / "examples" / "records.json").read_text(encoding="utf-8"))


def _make_verified_decision(
    *,
    record_id: str,
    target: dict,
    action: str = "approve",
    effective_at: str = "2026-11-20T00:00:00Z",
    reviewer: str = "alice-maintainer",
    resolution: dict | None = None,
) -> dict:
    body: dict = {
        "target_record_id": target["record_id"],
        "target_kind": target["kind"],
        "action": action,
        "target_sha256": canonical_sha256(target),
        "effective_at": effective_at,
        "authority": {
            "mode": "verified-repository-approval",
            "subject": reviewer,
        },
        "proof": {
            "type": "github-pr-review",
            "verified_repository_approval": True,
            "protected_branch": True,
            "reviewer": reviewer,
            "review_commit": "aaa111",
            "merge_commit": "bbb222",
            "reviewed_sha256": canonical_sha256(target),
        },
    }
    if resolution is not None:
        body["resolution"] = resolution
    return {
        "schema_version": "0.1-draft",
        "record_id": record_id,
        "kind": "governance_decision",
        "created_at": effective_at,
        "actor": {"type": "human", "id": reviewer},
        "body": body,
    }


def test_local_owner_assertion_never_activates_under_strict_policy() -> None:
    records = _fixture_records()
    ctx = {"repository": "sample/api-compat-demo", "paths": ["api/v1.py"], "languages": ["python"]}

    # Strict default policy: allow_local_assertions=False
    strict_state = project_effective_state(
        records, as_of="2026-11-21T00:00:00Z", policy=AuthorityPolicy()
    )
    res_strict = resolve_context(strict_state, ctx)
    assert res_strict.state == ResolutionState.NONE
    assert res_strict.practices == []
    assert res_strict.applications == []

    # Explicit local advisory policy: activates ONLY as local-owner-assertion
    local_policy = AuthorityPolicy(allow_local_assertions=True)
    local_state = project_effective_state(
        records, as_of="2026-11-21T00:00:00Z", policy=local_policy
    )
    res_local = resolve_context(local_state, ctx)
    assert res_local.state == ResolutionState.ACTIVE
    assert len(res_local.practices) == 1
    assert res_local.practices[0].authority_class == AuthorityClass.LOCAL_OWNER_ASSERTION
    assert res_local.practices[0].verified_repository_approval is False
    assert len(res_local.applications) == 1
    assert res_local.applications[0].verified_repository_approval is False


def test_verified_repository_approval_and_unauthorized_reviewer_rejected() -> None:
    records = _fixture_records()[:6]
    pv = records[2]
    app = records[4]
    # Replace local decisions with verified-repository-approval decisions
    records = [
        records[0],
        records[1],
        pv,
        app,
        _make_verified_decision(record_id="dec-pv-1", target=pv, reviewer="alice-maintainer"),
        _make_verified_decision(record_id="dec-app-1", target=app, reviewer="alice-maintainer"),
    ]
    ctx = {"repository": "sample/api-compat-demo", "paths": ["api/v1.py"], "languages": ["python"]}

    # Wrong reviewer policy -> non-activation
    wrong_policy = AuthorityPolicy(authorized_reviewers=frozenset({"bob-maintainer"}))
    res_wrong = resolve_context(
        project_effective_state(records, as_of="2026-11-21T00:00:00Z", policy=wrong_policy),
        ctx,
    )
    assert res_wrong.state == ResolutionState.NONE

    # Authorized reviewer -> active & verified
    ok_policy = AuthorityPolicy(authorized_reviewers=frozenset({"alice-maintainer"}))
    res_ok = resolve_context(
        project_effective_state(records, as_of="2026-11-21T00:00:00Z", policy=ok_policy),
        ctx,
    )
    assert res_ok.state == ResolutionState.ACTIVE
    assert res_ok.practices[0].verified_repository_approval is True
    assert res_ok.practices[0].authority_class == AuthorityClass.VERIFIED_REPOSITORY_APPROVAL
    assert len(res_ok.applications) == 1
    assert res_ok.applications[0].verified_repository_approval is True


def test_suspend_application_does_not_suspend_practice_but_retire_practice_invalidates_app() -> None:
    base = _fixture_records()
    ev, cand, pv, app = base[0], base[1], base[2], base[4]
    policy = AuthorityPolicy(authorized_reviewers=frozenset({"alice"}))
    ctx = {"repository": "sample/api-compat-demo", "paths": ["api/v1.py"], "languages": ["python"]}

    records = [
        ev,
        cand,
        pv,
        app,
        _make_verified_decision(
            record_id="d1", target=pv, action="approve", effective_at="2026-11-20T00:00:00Z", reviewer="alice"
        ),
        _make_verified_decision(
            record_id="d2", target=app, action="approve", effective_at="2026-11-20T00:00:00Z", reviewer="alice"
        ),
        _make_verified_decision(
            record_id="d3", target=app, action="suspend", effective_at="2026-11-20T02:00:00Z", reviewer="alice"
        ),
        _make_verified_decision(
            record_id="d4", target=app, action="restore", effective_at="2026-11-20T04:00:00Z", reviewer="alice"
        ),
        _make_verified_decision(
            record_id="d5", target=pv, action="retire", effective_at="2026-11-20T06:00:00Z", reviewer="alice"
        ),
        # Attempt to restore after retire must have no effect (retire is terminal)
        _make_verified_decision(
            record_id="d6", target=pv, action="restore", effective_at="2026-11-20T08:00:00Z", reviewer="alice"
        ),
    ]

    # At T=03:00: app is suspended, practice is STILL active
    r_03 = resolve_context(
        project_effective_state(records, as_of="2026-11-20T03:00:00Z", policy=policy), ctx
    )
    assert r_03.state == ResolutionState.ACTIVE
    assert len(r_03.practices) == 1
    assert len(r_03.applications) == 0
    assert app["record_id"] in r_03.suspended_records

    # At T=05:00: app restored -> both active
    r_05 = resolve_context(
        project_effective_state(records, as_of="2026-11-20T05:00:00Z", policy=policy), ctx
    )
    assert r_05.state == ResolutionState.ACTIVE
    assert len(r_05.applications) == 1

    # At T=09:00: practice retired -> both practice and app inactive despite attempted restore
    r_09 = resolve_context(
        project_effective_state(records, as_of="2026-11-20T09:00:00Z", policy=policy), ctx
    )
    assert r_09.state == ResolutionState.NONE
    assert r_09.practices == []
    assert r_09.applications == []


def test_candidate_conflict_never_disables_approved_practice() -> None:
    """Non-interference invariant: candidate vs active practice conflict cannot deactivate practice."""
    records = _fixture_records()
    # Replace fixture-0008 relation with 'contradicts' between candidate and approved practice
    records[7]["body"]["relation"] = "contradicts"
    ctx = {"repository": "sample/api-compat-demo", "paths": ["api/v1.py"], "languages": ["python"]}
    policy = AuthorityPolicy(allow_local_assertions=True)

    res = resolve_context(
        project_effective_state(records, as_of="2026-11-21T00:00:00Z", policy=policy), ctx
    )
    assert res.state == ResolutionState.ACTIVE
    assert len(res.practices) == 1
    assert res.practices[0].record_id == "fixture-0003"


def test_approved_vs_approved_conflict_is_ambiguous_until_adjudicated() -> None:
    base = _fixture_records()
    ev, cand, pv1 = base[0], base[1], base[2]
    pv2 = copy.deepcopy(pv1)
    pv2["record_id"] = "pv-second"
    pv2["body"]["practice_id"] = "sample-practice-api-prune"
    pv2["body"]["statement"] = "Deprecated API fields must be removed immediately."

    conflict = {
        "schema_version": "0.1-draft",
        "record_id": "conf-1",
        "kind": "conflict",
        "created_at": "2026-11-20T01:00:00Z",
        "actor": {"type": "service", "id": "conflict-detector"},
        "body": {
            "left_record_id": pv1["record_id"],
            "right_record_id": pv2["record_id"],
            "relation": "contradicts",
            "evidence_ids": [ev["record_id"]],
            "detector": {"name": "test-detector", "version": "0.1"},
        },
    }

    policy = AuthorityPolicy(authorized_reviewers=frozenset({"alice"}))
    ctx = {"repository": "sample/api-compat-demo", "paths": ["api/v1.py"], "languages": ["python"]}

    records = [
        ev,
        cand,
        pv1,
        pv2,
        _make_verified_decision(record_id="d1", target=pv1, reviewer="alice"),
        _make_verified_decision(record_id="d2", target=pv2, reviewer="alice"),
        conflict,
    ]

    # Without adjudication -> ambiguous (never silently picks a winner)
    res_unadj = resolve_context(
        project_effective_state(records, as_of="2026-11-21T00:00:00Z", policy=policy), ctx
    )
    assert res_unadj.state == ResolutionState.AMBIGUOUS
    assert len(res_unadj.conflicts) == 1

    # Add authorized adjudication preferring left
    adj = _make_verified_decision(
        record_id="d-adj",
        target=conflict,
        action="adjudicate",
        effective_at="2026-11-20T02:00:00Z",
        reviewer="alice",
        resolution={
            "disposition": "prefer-left",
            "explanation": "Backward compatibility takes precedence over pruning.",
        },
    )
    res_adj = resolve_context(
        project_effective_state([*records, adj], as_of="2026-11-21T00:00:00Z", policy=policy), ctx
    )
    assert res_adj.state == ResolutionState.ACTIVE
    assert [p.record_id for p in res_adj.practices] == [pv1["record_id"]]


def test_deterministic_replay_independent_of_input_order() -> None:
    records = _fixture_records()
    policy = AuthorityPolicy(allow_local_assertions=True)
    ctx = {"repository": "sample/api-compat-demo", "paths": ["api/v1.py"], "languages": ["python"]}

    r1 = resolve_context(
        project_effective_state(records, as_of="2026-11-21T00:00:00Z", policy=policy), ctx
    )
    r2 = resolve_context(
        project_effective_state(list(reversed(records)), as_of="2026-11-21T00:00:00Z", policy=policy),
        ctx,
    )
    assert r1.to_dict() == r2.to_dict()


def test_engine_logical_operations_query_propose_resolve_observe() -> None:
    records = _fixture_records()[:6]
    store = InMemoryStore(records)
    engine = OneMindEngine(store, policy=AuthorityPolicy(allow_local_assertions=True))

    q = engine.query({"kinds": ["practice_version"]}, as_of="2026-11-21T00:00:00Z")
    assert len(q.records) == 1
    assert q.records[0].authority_class == AuthorityClass.LOCAL_OWNER_ASSERTION

    # Propose dry-run does not mutate store
    new_cand = copy.deepcopy(records[1])
    new_cand["record_id"] = "cand-new-1"
    dry = engine.propose(new_cand, write=False)
    assert dry.valid is True
    assert dry.written is False
    assert store.get_record("cand-new-1") is None

    # Propose with write persists inert candidate (still never activates)
    written = engine.propose(new_cand, write=True)
    assert written.valid is True
    assert written.written is True
    assert store.get_record("cand-new-1") is not None

    q_cand = engine.query({"record_ids": ["cand-new-1"]})
    assert q_cand.records[0].authority_class == AuthorityClass.INERT_CANDIDATE
    assert q_cand.records[0].lifecycle == "inert"

    # Observe appends deduplicated outcome event without touching governance
    ev = _fixture_records()[6]
    obs1 = engine.observe(ev)
    assert obs1.appended is True
    obs2 = engine.observe(ev)
    assert obs2.appended is False
    assert obs2.deduplicated is True
