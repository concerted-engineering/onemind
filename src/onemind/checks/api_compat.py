"""Deterministic JSON API schema compatibility checker (offline, read-only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from onemind.adapters.interfaces import ApplicationAdapter, CheckFinding, CheckResult


def _type_signature(schema_node: Any) -> str:
    """Return a canonical comparable type signature for a schema field definition."""
    if not isinstance(schema_node, dict):
        return type(schema_node).__name__
    t = schema_node.get("type")
    if isinstance(t, list):
        sig = "|".join(sorted(str(x) for x in t))
    elif isinstance(t, str):
        sig = t
    else:
        sig = "any"
    if schema_node.get("nullable") is True and "null" not in sig:
        sig = f"{sig}|null"
    if "enum" in schema_node and isinstance(schema_node["enum"], list):
        sig = f"{sig}:enum({','.join(sorted(str(v) for v in schema_node['enum']))})"
    return sig


def _is_type_compatible(base_node: dict[str, Any], prop_node: dict[str, Any]) -> bool:
    """Return True if proposed type is backward-compatible for API response consumers."""
    base_sig = _type_signature(base_node)
    prop_sig = _type_signature(prop_node)
    if base_sig == prop_sig:
        return True
    # Widening enum on a response can break strict consumers; changing type (e.g. string->int) breaks consumers;
    # making a non-nullable response field nullable breaks non-null consumers.
    # Narrowing a nullable response field to non-nullable of the same base type is compatible.
    return base_sig == f"{prop_sig}|null"


def _compare_object_schema(
    base_obj: dict[str, Any],
    prop_obj: dict[str, Any],
    prefix: str,
    findings: list[CheckFinding],
) -> None:
    base_props = base_obj.get("properties")
    prop_props = prop_obj.get("properties")

    if isinstance(base_props, dict):
        if not isinstance(prop_props, dict):
            findings.append(
                CheckFinding(
                    code="API_PROPERTIES_REMOVED",
                    severity="error",
                    message=f"Object properties block removed at '{prefix}'.",
                    location=prefix,
                )
            )
            return

        for field_name in sorted(base_props.keys()):
            field_loc = f"{prefix}.{field_name}" if prefix else field_name
            if field_name not in prop_props:
                findings.append(
                    CheckFinding(
                        code="API_FIELD_REMOVED",
                        severity="error",
                        message=(
                            f"Established public API response field '{field_loc}' was removed "
                            "in the proposed schema."
                        ),
                        location=field_loc,
                        details={"field": field_name, "baseline_type": _type_signature(base_props[field_name])},
                    )
                )
                continue

            b_node = base_props[field_name]
            p_node = prop_props[field_name]
            if isinstance(b_node, dict) and isinstance(p_node, dict):
                if not _is_type_compatible(b_node, p_node):
                    findings.append(
                        CheckFinding(
                            code="API_FIELD_TYPE_CHANGED",
                            severity="error",
                            message=(
                                f"Public API field '{field_loc}' changed type incompatibly from "
                                f"{_type_signature(b_node)!r} to {_type_signature(p_node)!r}."
                            ),
                            location=field_loc,
                            details={
                                "baseline_type": _type_signature(b_node),
                                "proposed_type": _type_signature(p_node),
                            },
                        )
                    )
                # Recurse into nested object properties
                if isinstance(b_node.get("properties"), dict):
                    _compare_object_schema(b_node, p_node, field_loc, findings)
                # Recurse into array item schemas
                if isinstance(b_node.get("items"), dict) and isinstance(p_node.get("items"), dict):
                    _compare_object_schema(b_node["items"], p_node["items"], f"{field_loc}[]", findings)

        base_req = set(base_obj.get("required") or [])
        prop_req = set(prop_obj.get("required") or [])
        for req_field in sorted(base_req - prop_req):
            if req_field in prop_props:
                field_loc = f"{prefix}.{req_field}" if prefix else req_field
                findings.append(
                    CheckFinding(
                        code="API_REQUIRED_FIELD_DROPPED",
                        severity="error",
                        message=(
                            f"Guaranteed required response field '{field_loc}' was downgraded "
                            "to optional in the proposed schema."
                        ),
                        location=field_loc,
                    )
                )


class ApiCompatJsonChecker(ApplicationAdapter):
    """Deterministic checker comparing baseline vs proposed JSON API response schemas."""

    checker_id: str = "builtin:api-compat-json"
    checker_version: str = "0.1.0"
    description: str = (
        "Deterministically compares a baseline and proposed JSON API response schema "
        "to detect removed fields, removed endpoints, incompatible field type changes, "
        "and downgraded required fields."
    )
    capabilities: tuple[str, ...] = (
        "Detects removal of established top-level and nested response fields.",
        "Detects removal of established endpoints in an 'endpoints' map.",
        "Detects incompatible response field type changes (e.g., string -> integer, non-null -> nullable).",
        "Detects downgrading previously required response fields to optional.",
        "Allows additive backward-compatible fields and new endpoints.",
    )
    known_limitations: tuple[str, ...] = (
        "Does not execute application code or prove runtime HTTP serialization matches the schema.",
        "Does not verify semantic meaning changes when field names and JSON types remain unchanged.",
        "Does not inspect HTTP status codes, headers, authentication, or query parameter contracts.",
        "Requires explicit baseline and proposed JSON schema files; missing or invalid JSON returns 'indeterminate'.",
    )

    def _load_json_input(self, value: Any, label: str) -> tuple[dict[str, Any] | None, str | None, str]:
        if isinstance(value, dict):
            return value, None, f"<in-memory:{label}>"
        if isinstance(value, (str, Path)):
            path = Path(value)
            if not path.is_file():
                return None, f"Missing {label} schema file: {path}", str(path)
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return None, f"Failed to parse {label} schema file {path}: {exc}", str(path)
            if not isinstance(raw, dict):
                return None, f"{label} schema file {path} must contain a JSON object.", str(path)
            return raw, None, str(path)
        return None, f"Unsupported or missing {label} input.", f"<missing:{label}>"

    def evaluate(self, inputs: dict[str, Any]) -> CheckResult:
        base_schema, base_err, base_label = self._load_json_input(inputs.get("base"), "baseline")
        prop_schema, prop_err, prop_label = self._load_json_input(inputs.get("proposed"), "proposed")
        evaluated = [base_label, prop_label]

        if base_err or prop_err or base_schema is None or prop_schema is None:
            reasons = [r for r in (base_err, prop_err) if r]
            return CheckResult(
                checker_id=self.checker_id,
                checker_version=self.checker_version,
                status="indeterminate",
                summary=f"Cannot evaluate compatibility: {'; '.join(reasons)}",
                findings=[],
                capabilities=list(self.capabilities),
                known_limitations=list(self.known_limitations),
                inputs_evaluated=evaluated,
            )

        findings: list[CheckFinding] = []

        # Support either multi-endpoint schema {"endpoints": {"GET /v1/...": {"response": {...}}}}
        # or single JSON Schema object {"properties": {...}, "required": [...]}
        if isinstance(base_schema.get("endpoints"), dict):
            base_endpoints = base_schema["endpoints"]
            prop_endpoints = prop_schema.get("endpoints")
            if not isinstance(prop_endpoints, dict):
                return CheckResult(
                    checker_id=self.checker_id,
                    checker_version=self.checker_version,
                    status="indeterminate",
                    summary="Proposed schema is missing top-level 'endpoints' object present in baseline.",
                    findings=[],
                    capabilities=list(self.capabilities),
                    known_limitations=list(self.known_limitations),
                    inputs_evaluated=evaluated,
                )
            for ep_name in sorted(base_endpoints.keys()):
                if ep_name not in prop_endpoints:
                    findings.append(
                        CheckFinding(
                            code="API_ENDPOINT_REMOVED",
                            severity="error",
                            message=f"Established public API endpoint '{ep_name}' was removed.",
                            location=ep_name,
                        )
                    )
                    continue
                b_ep = base_endpoints[ep_name]
                p_ep = prop_endpoints[ep_name]
                if isinstance(b_ep, dict) and isinstance(p_ep, dict):
                    b_resp = b_ep.get("response", b_ep)
                    p_resp = p_ep.get("response", p_ep)
                    if isinstance(b_resp, dict) and isinstance(p_resp, dict):
                        _compare_object_schema(b_resp, p_resp, f"{ep_name}.response", findings)
        elif isinstance(base_schema.get("properties"), dict):
            _compare_object_schema(base_schema, prop_schema, "response", findings)
        else:
            return CheckResult(
                checker_id=self.checker_id,
                checker_version=self.checker_version,
                status="indeterminate",
                summary=(
                    "Baseline JSON file does not declare 'endpoints' or 'properties'; "
                    "cannot determine API compatibility."
                ),
                findings=[],
                capabilities=list(self.capabilities),
                known_limitations=list(self.known_limitations),
                inputs_evaluated=evaluated,
            )

        if findings:
            return CheckResult(
                checker_id=self.checker_id,
                checker_version=self.checker_version,
                status="fail",
                summary=(
                    f"Detected {len(findings)} backward-incompatible API schema change(s) "
                    "against the baseline schema."
                ),
                findings=findings,
                capabilities=list(self.capabilities),
                known_limitations=list(self.known_limitations),
                inputs_evaluated=evaluated,
            )

        return CheckResult(
            checker_id=self.checker_id,
            checker_version=self.checker_version,
            status="pass",
            summary="No backward-incompatible field/type removals detected in compared JSON schemas.",
            findings=[],
            capabilities=list(self.capabilities),
            known_limitations=list(self.known_limitations),
            inputs_evaluated=evaluated,
        )
