"""Conservative, fail-closed scope matching, containment, and overlap semantics."""

from __future__ import annotations

import fnmatch
import posixpath
import re
from collections.abc import Mapping
from typing import Any

KNOWN_SCOPE_KEYS: frozenset[str] = frozenset({"repository", "paths", "languages", "conditions"})
_UNSUPPORTED_GLOB_RE = re.compile(r"[\{\}\[\]]")


def normalize_rel_path(path: str) -> str:
    """Normalize a repository-relative POSIX path without allowing parent traversal."""
    cleaned = path.replace("\\", "/")
    if cleaned.startswith("/") or ".." in cleaned.split("/"):
        return ""
    cleaned = cleaned.lstrip("./")
    if not cleaned:
        return ""
    norm = posixpath.normpath(cleaned)
    if norm in (".", "..") or norm.startswith("../"):
        return ""
    return norm


def validate_scope_structure(scope: Any) -> tuple[bool, str]:
    """Validate that a scope/applicability object is unambiguous and contains no unknown keys.

    Fail-closed: unrecognized constraint keys, empty filter lists, path traversal, or
    unsupported glob syntax return (False, reason).
    """
    if not isinstance(scope, Mapping):
        return False, "Scope must be a JSON object."

    unknown_keys = sorted(set(scope.keys()) - KNOWN_SCOPE_KEYS)
    if unknown_keys:
        return (
            False,
            f"Ambiguous scope contains unrecognized constraint key(s): {unknown_keys!r}.",
        )

    repo = scope.get("repository")
    if not isinstance(repo, str) or not repo.strip():
        return False, "Scope must specify a non-empty string 'repository'."

    if "paths" in scope:
        paths = scope["paths"]
        if not isinstance(paths, (list, tuple)) or len(paths) == 0:
            return False, "Scope 'paths' must be a non-empty list of glob patterns when specified."
        for pat in paths:
            if not isinstance(pat, str) or not pat.strip():
                return False, f"Invalid empty path pattern in scope: {pat!r}."
            norm_pat = pat.replace("\\", "/")
            if norm_pat.startswith("/") or ".." in norm_pat.split("/"):
                return (
                    False,
                    f"Unsafe path pattern {pat!r} in scope (absolute path or parent traversal).",
                )
            if _UNSUPPORTED_GLOB_RE.search(norm_pat):
                return (
                    False,
                    f"Ambiguous/unsupported glob syntax in path pattern {pat!r}; "
                    "only literal paths, '*', '?', and '**' are supported in v0.1-draft.",
                )

    if "languages" in scope:
        langs = scope["languages"]
        if not isinstance(langs, (list, tuple)) or len(langs) == 0:
            return False, "Scope 'languages' must be a non-empty list when specified."
        if any(not isinstance(lang, str) or not lang.strip() for lang in langs):
            return False, "Scope 'languages' contains empty or non-string entry."

    if "conditions" in scope:
        conds = scope["conditions"]
        if not isinstance(conds, (list, tuple)) or len(conds) == 0:
            return False, "Scope 'conditions' must be a non-empty list when specified."
        if any(not isinstance(c, str) or not c.strip() for c in conds):
            return False, "Scope 'conditions' contains empty or non-string entry."

    return True, ""


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert a POSIX glob pattern supporting '*', '?', and '**' to a compiled regex."""
    norm = pattern.replace("\\", "/")
    if norm.startswith("./"):
        norm = norm[2:]
    i = 0
    n = len(norm)
    parts: list[str] = ["^"]
    while i < n:
        ch = norm[i]
        if ch == "*":
            if i + 1 < n and norm[i + 1] == "*":
                if i + 2 < n and norm[i + 2] == "/":
                    parts.append("(?:.*/)?")
                    i += 3
                else:
                    parts.append(".*")
                    i += 2
            else:
                parts.append("[^/]*")
                i += 1
        elif ch == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(ch))
            i += 1
    parts.append("$")
    return re.compile("".join(parts))


def path_matches_glob(path: str, pattern: str) -> bool:
    """Return True if a relative POSIX path matches a glob pattern (supporting '**')."""
    norm_path = normalize_rel_path(path)
    if not norm_path:
        return False
    return bool(glob_to_regex(pattern).match(norm_path))


def _is_glob_covered_by(child_glob: str, parent_glob: str) -> bool:
    """Conservative check whether every path matched by child_glob is matched by parent_glob.

    Fail-safe: returns False when containment cannot be proven statically.
    """
    c = child_glob.replace("\\", "/").lstrip("./")
    p = parent_glob.replace("\\", "/").lstrip("./")
    if _UNSUPPORTED_GLOB_RE.search(c) or _UNSUPPORTED_GLOB_RE.search(p):
        return False
    if c == p:
        return True
    if p in ("**", "**/*"):
        return True
    # Parent is prefix/** (e.g., "api/**")
    if p.endswith("/**"):
        prefix = p[:-3]
        if not any(tok in prefix for tok in ("*", "?")) and (
            c == prefix or c.startswith(prefix + "/")
        ):
            return True
    # Parent is prefix/**/*.ext (e.g., "api/**/*.json")
    if "/**/" in p:
        p_prefix, p_suffix = p.split("/**/", 1)
        if (
            not any(tok in p_prefix for tok in ("*", "?"))
            and "**" not in p_suffix
            and c.startswith(p_prefix + "/")
        ):
            c_tail = c[len(p_prefix) + 1 :]
            c_base = posixpath.basename(c_tail)
            if not any(tok in c_base for tok in ("*", "?")) and fnmatch.fnmatchcase(
                c_base, p_suffix
            ):
                return True
            if c_tail.endswith("/" + p_suffix) or c_tail == p_suffix:
                return True
    # Parent is prefix/* (single directory level)
    if p.endswith("/*") and "**" not in p:
        prefix = p[:-2]
        if not any(tok in prefix for tok in ("*", "?")) and c.startswith(prefix + "/"):
            rest = c[len(prefix) + 1 :]
            if "/" not in rest and "**" not in rest:
                return True
    # Child is a literal path (no wildcards) -> test directly against parent glob
    if not any(tok in c for tok in ("*", "?")):
        return path_matches_glob(c, p)
    # Fallback: same literal directory and exact/narrower filename match
    if "**" not in c and "**" not in p:
        c_dir, c_base = posixpath.split(c)
        p_dir, p_base = posixpath.split(p)
        if c_dir == p_dir and fnmatch.fnmatchcase(c_base, p_base) and "*" not in c_base:
            return True
    return False


def is_scope_subset(child_scope: Mapping[str, Any], parent_scope: Mapping[str, Any]) -> tuple[bool, str]:
    """Return (True, '') iff child_scope is provably no wider than parent_scope.

    Used to enforce that an Application's applicability never exceeds its PracticeVersion's scope
    and that scoped exceptions stay within their target practice scope.
    Fail-closed: unrecognized constraints or unprovable containment returns (False, reason).
    """
    p_ok, p_reason = validate_scope_structure(parent_scope)
    if not p_ok:
        return False, f"Invalid parent scope: {p_reason}"
    c_ok, c_reason = validate_scope_structure(child_scope)
    if not c_ok:
        return False, f"Invalid child scope: {c_reason}"

    parent_repo = parent_scope["repository"]
    child_repo = child_scope["repository"]
    if child_repo != parent_repo:
        return (
            False,
            f"Application repository {child_repo!r} does not match practice repository {parent_repo!r}.",
        )

    parent_paths = parent_scope.get("paths")
    child_paths = child_scope.get("paths")
    if parent_paths:
        if not child_paths:
            return (
                False,
                "Application omits 'paths', which would widen beyond practice path scope.",
            )
        for cp in child_paths:
            if not any(_is_glob_covered_by(cp, pp) for pp in parent_paths):
                return (
                    False,
                    f"Application path pattern {cp!r} exceeds practice paths {list(parent_paths)!r}.",
                )

    parent_langs = parent_scope.get("languages")
    child_langs = child_scope.get("languages")
    if parent_langs:
        if not child_langs:
            return (
                False,
                "Application omits 'languages', which would widen beyond practice language scope.",
            )
        parent_lang_set = {lang.lower() for lang in parent_langs}
        child_lang_set = {lang.lower() for lang in child_langs}
        extra = sorted(child_lang_set - parent_lang_set)
        if extra:
            return (
                False,
                f"Application languages {extra!r} exceed practice languages {list(parent_langs)!r}.",
            )

    parent_conds = parent_scope.get("conditions")
    child_conds = child_scope.get("conditions")
    if parent_conds:
        parent_cond_set = set(parent_conds)
        child_cond_set = set(child_conds or [])
        missing = sorted(parent_cond_set - child_cond_set)
        if missing:
            return (
                False,
                f"Application drops required practice conditions {missing!r}.",
            )

    return True, ""


def evaluate_scope_applicability(
    scope: Mapping[str, Any], context: Mapping[str, Any]
) -> tuple[str, str]:
    """Evaluate scope applicability against a context, failing closed on ambiguity.

    Returns one of:
      - ("match", "")
      - ("no-match", reason)
      - ("ambiguous", reason)  # malformed/unknown scope constraints or unsafe context paths
    """
    ok, reason = validate_scope_structure(scope)
    if not ok:
        return "ambiguous", reason

    ctx_repo = context.get("repository")
    if not isinstance(ctx_repo, str) or not ctx_repo.strip():
        return "ambiguous", "Context missing non-empty 'repository'."
    if scope["repository"] != ctx_repo:
        return "no-match", "Repository does not match."

    if "paths" in context and context["paths"] is not None:
        ctx_paths = context["paths"]
        if not isinstance(ctx_paths, (list, tuple)):
            return "ambiguous", "Context 'paths' must be a list of relative file paths."
        for cp in ctx_paths:
            if not isinstance(cp, str) or not normalize_rel_path(cp):
                return "ambiguous", f"Unsafe or malformed path {cp!r} in resolution context."

    scope_paths = scope.get("paths")
    if scope_paths:
        ctx_paths = context.get("paths") or []
        if not ctx_paths:
            return "no-match", "Practice requires path match; context provides no paths."
        if not any(path_matches_glob(cp, sp) for cp in ctx_paths for sp in scope_paths):
            return "no-match", "No context path matched scope paths."

    scope_langs = scope.get("languages")
    if scope_langs:
        ctx_langs = context.get("languages")
        if ctx_langs is not None and not isinstance(ctx_langs, (list, tuple)):
            return "ambiguous", "Context 'languages' must be a list of strings."
        if not ctx_langs:
            return "no-match", "Practice requires language match; context provides no languages."
        if not ({x.lower() for x in scope_langs} & {str(x).lower() for x in ctx_langs}):
            return "no-match", "No context language matched scope languages."

    scope_conds = scope.get("conditions")
    if scope_conds:
        ctx_conds = context.get("conditions")
        if ctx_conds is not None and not isinstance(ctx_conds, (list, tuple)):
            return "ambiguous", "Context 'conditions' must be a list of strings."
        if not set(scope_conds).issubset(set(ctx_conds or [])):
            return "no-match", "Required scope conditions not satisfied by context."

    return "match", ""


def scope_matches_context(scope: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    """Return True iff scope unambiguously matches context (fails closed on ambiguity)."""
    status, _ = evaluate_scope_applicability(scope, context)
    return status == "match"


def scopes_may_overlap(scope_a: Mapping[str, Any], scope_b: Mapping[str, Any]) -> bool:
    """Conservative overlap check between two scopes."""
    if scope_a.get("repository") != scope_b.get("repository"):
        return False

    langs_a = scope_a.get("languages")
    langs_b = scope_b.get("languages")
    if langs_a and langs_b and not ({x.lower() for x in langs_a} & {x.lower() for x in langs_b}):
        return False

    paths_a = scope_a.get("paths")
    paths_b = scope_b.get("paths")
    if not paths_a or not paths_b:
        return True

    for pa in paths_a:
        for pb in paths_b:
            if _is_glob_covered_by(pa, pb) or _is_glob_covered_by(pb, pa):
                return True
            a_head = pa.split("/", 1)[0]
            b_head = pb.split("/", 1)[0]
            if a_head == b_head or "*" in a_head or "*" in b_head:
                return True
    return False
