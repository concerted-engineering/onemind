"""Safe registry of audited built-in deterministic checkers.

SECURITY INVARIANT: OneMind never executes arbitrary code, scripts, or shell commands
referenced in imported records or untrusted files. Only explicitly registered built-in
checkers may be dispatched by identifier.
"""

from __future__ import annotations

from onemind.adapters.interfaces import ApplicationAdapter
from onemind.checks.api_compat import ApiCompatJsonChecker

_API_COMPAT = ApiCompatJsonChecker()

_BUILTIN_CHECKERS: dict[str, ApplicationAdapter] = {
    "builtin:api-compat-json": _API_COMPAT,
    "builtin:api-compat-json/0.1": _API_COMPAT,
    "builtin:api-compat-json/0.1.0": _API_COMPAT,
    "api-compat-json": _API_COMPAT,
}


def resolve_checker(implementation_id: str) -> ApplicationAdapter | None:
    """Resolve a built-in checker by registered identifier, or return None if unknown.

    Never imports or executes arbitrary filesystem paths or shell strings.
    """
    return _BUILTIN_CHECKERS.get(implementation_id)


def list_builtin_checkers() -> list[dict[str, object]]:
    """Return metadata for all audited built-in deterministic checkers."""
    seen: set[str] = set()
    out: list[dict[str, object]] = []
    for checker in _BUILTIN_CHECKERS.values():
        if checker.checker_id in seen:
            continue
        seen.add(checker.checker_id)
        out.append(
            {
                "checker_id": checker.checker_id,
                "checker_version": checker.checker_version,
                "description": checker.description,
                "capabilities": list(checker.capabilities),
                "known_limitations": list(checker.known_limitations),
            }
        )
    return out
