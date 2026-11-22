"""Conservative scope matching, containment, and overlap semantics."""

from __future__ import annotations

import fnmatch
import posixpath
import re
from typing import Any


def normalize_rel_path(path: str) -> str:
    """Normalize a repository-relative POSIX path without allowing parent traversal."""
    cleaned = path.replace("\\", "/").lstrip("./")
    if not cleaned:
        return ""
    norm = posixpath.normpath(cleaned)
    if norm == ".":
        return ""
    return norm


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
                # Handle '**/' matching zero or more directories
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
    # Fallback: fnmatch only if neither contains '**' and directory prefixes match literally
    if "**" not in c and "**" not in p:
        c_dir, c_base = posixpath.split(c)
        p_dir, p_base = posixpath.split(p)
        if c_dir == p_dir and fnmatch.fnmatchcase(c_base, p_base) and "*" not in c_base:
            return True
    return False


def is_scope_subset(child_scope: dict[str, Any], parent_scope: dict[str, Any]) -> tuple[bool, str]:
    """Return (True, '') iff child_scope is provably no wider than parent_scope.

    Used to enforce that an Application's applicability never exceeds its PracticeVersion's scope.
    Fail-safe: ambiguous or unprovable containment returns (False, reason).
    """
    parent_repo = parent_scope.get("repository")
    child_repo = child_scope.get("repository")
    if not parent_repo or not child_repo:
        return False, "Both practice scope and application applicability must specify 'repository'."
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
                    f"Application path pattern {cp!r} exceeds practice paths {parent_paths!r}.",
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
                f"Application languages {extra!r} exceed practice languages {parent_langs!r}.",
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


def scope_matches_context(scope: dict[str, Any], context: dict[str, Any]) -> bool:
    """Return True if a practice/application scope matches a resolution context.

    Context fields:
      - repository: str (required)
      - paths: list[str] (optional; if scope has paths, at least one context path must match)
      - languages: list[str] (optional; if scope has languages, intersection must be non-empty)
      - conditions: list[str] (optional; if scope has conditions, all must be present in context)
    """
    ctx_repo = context.get("repository")
    if not ctx_repo or scope.get("repository") != ctx_repo:
        return False

    scope_paths = scope.get("paths")
    if scope_paths:
        ctx_paths = context.get("paths") or []
        if not ctx_paths:
            return False
        if not any(path_matches_glob(cp, sp) for cp in ctx_paths for sp in scope_paths):
            return False

    scope_langs = scope.get("languages")
    if scope_langs:
        ctx_langs = context.get("languages") or []
        if not ctx_langs:
            return False
        if not ({x.lower() for x in scope_langs} & {x.lower() for x in ctx_langs}):
            return False

    scope_conds = scope.get("conditions")
    if scope_conds:
        ctx_conds = set(context.get("conditions") or [])
        if not set(scope_conds).issubset(ctx_conds):
            return False

    return True


def scopes_may_overlap(scope_a: dict[str, Any], scope_b: dict[str, Any]) -> bool:
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
            # Check shared static prefix if neither covers the other
            a_head = pa.split("/", 1)[0]
            b_head = pb.split("/", 1)[0]
            if a_head == b_head or "*" in a_head or "*" in b_head:
                return True
    return False
