"""External authority policy configuration for OneMind governance evaluation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AuthorityPolicy:
    """Versioned repository authority policy external to individual records.

    Governance decisions carry proof claims; this policy defines which
    reviewers and verification requirements qualify for activation.
    """

    policy_version: str = "0.1-draft-default"
    repository: str | None = None
    authorized_reviewers: frozenset[str] = field(default_factory=frozenset)
    require_protected_merge: bool = True
    allow_local_assertions: bool = False
    active_routes: frozenset[str] = field(default_factory=lambda: frozenset({"preflight"}))

    def with_local_assertions(self, allow: bool) -> AuthorityPolicy:
        return AuthorityPolicy(
            policy_version=self.policy_version,
            repository=self.repository,
            authorized_reviewers=self.authorized_reviewers,
            require_protected_merge=self.require_protected_merge,
            allow_local_assertions=allow,
            active_routes=self.active_routes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "repository": self.repository,
            "authorized_reviewers": sorted(self.authorized_reviewers),
            "require_protected_merge": self.require_protected_merge,
            "allow_local_assertions": self.allow_local_assertions,
            "active_routes": sorted(self.active_routes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuthorityPolicy:
        reviewers = data.get("authorized_reviewers", [])
        routes = data.get("active_routes", ["preflight"])
        return cls(
            policy_version=str(data.get("policy_version", "0.1-draft-custom")),
            repository=str(data["repository"]) if data.get("repository") else None,
            authorized_reviewers=frozenset(str(r) for r in reviewers),
            require_protected_merge=bool(data.get("require_protected_merge", True)),
            allow_local_assertions=bool(data.get("allow_local_assertions", False)),
            active_routes=frozenset(str(r) for r in routes),
        )


def load_policy(repo_path: Path | str, *, store_rel_dir: str = ".onemind") -> AuthorityPolicy:
    """Load `.onemind/policy.json` if present, otherwise return conservative default policy."""
    policy_path = Path(repo_path).resolve() / store_rel_dir / "policy.json"
    if not policy_path.is_file():
        return AuthorityPolicy()
    data = json.loads(policy_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Policy file {policy_path} must contain a JSON object.")
    return AuthorityPolicy.from_dict(data)
