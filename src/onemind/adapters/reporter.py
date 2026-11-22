"""Pure, deterministic summary reporter implementing the Reporter interface."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from onemind.adapters.interfaces import Reporter
from onemind.governance.policy import AuthorityPolicy
from onemind.governance.projection import project_effective_state
from onemind.protocol.models import COMPUTATION_VERSION, RecordDict, RecordKind, utc_now_iso


class SummaryReporter(Reporter):
    """Pure reporter projecting a pinned record set into an auditable summary."""

    report_version: str = "onemind-summary-report/0.1-draft"

    def __init__(self, *, policy: AuthorityPolicy | None = None) -> None:
        self.policy = policy or AuthorityPolicy()

    def render(
        self,
        records: Sequence[RecordDict],
        *,
        commit_ref: str | None = None,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        eval_as_of = as_of or utc_now_iso()
        eff = project_effective_state(
            records,
            as_of=eval_as_of,
            policy=self.policy,
            commit_ref=commit_ref,
        )
        kind_counts = Counter(str(r.get("kind", "unknown")) for r in records)
        outcome_counts = Counter(
            str(r.get("body", {}).get("event_type", "unknown"))
            for r in records
            if r.get("kind") == RecordKind.OUTCOME_EVENT
        )
        synthetic_outcomes = sum(
            1
            for r in records
            if r.get("kind") == RecordKind.OUTCOME_EVENT
            and r.get("body", {}).get("details", {}).get("synthetic") is True
        )

        return {
            "report_version": self.report_version,
            "computation_version": COMPUTATION_VERSION,
            "commit_ref": commit_ref,
            "as_of": eval_as_of,
            "policy_version": self.policy.policy_version,
            "indeterminate": eff.indeterminate,
            "record_counts_by_kind": dict(sorted(kind_counts.items())),
            "practice_lifecycles": {
                pid: st.lifecycle for pid, st in sorted(eff.practice_states.items())
            },
            "application_lifecycles": {
                aid: st.lifecycle for aid, st in sorted(eff.application_states.items())
            },
            "outcome_counts_by_type": dict(sorted(outcome_counts.items())),
            "synthetic_outcome_count": synthetic_outcomes,
            "disclaimer": (
                "Synthetic or local observations do not constitute empirical evidence "
                "of organizational engineering improvement."
            ),
        }
