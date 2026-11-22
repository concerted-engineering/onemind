"""JSON Schema 2020-12 structural validation for OneMind v0.1-draft records."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from onemind.protocol.models import ALL_RECORD_KINDS, SCHEMA_VERSION, is_valid_rfc3339


@dataclass(frozen=True)
class SchemaError:
    """A single structural schema validation failure."""

    code: str
    message: str
    record_id: str | None = None
    path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.record_id is not None:
            out["record_id"] = self.record_id
        if self.path is not None:
            out["path"] = self.path
        if self.details:
            out["details"] = self.details
        return out


@lru_cache(maxsize=1)
def load_record_schema() -> dict[str, Any]:
    """Load the bundled OneMind v0.1-draft JSON Schema."""
    schema_text = (
        resources.files("onemind.protocol")
        .joinpath("record.schema.json")
        .read_text(encoding="utf-8")
    )
    loaded: dict[str, Any] = json.loads(schema_text)
    return loaded


def _build_format_checker() -> FormatChecker:
    checker = FormatChecker()

    def _check_datetime(instance: object) -> bool:
        if not isinstance(instance, str):
            return True
        return is_valid_rfc3339(instance)

    checker.checks("date-time", ValueError)(_check_datetime)
    return checker


@lru_cache(maxsize=1)
def _validators_by_kind() -> dict[str, Draft202012Validator]:
    schema = load_record_schema()
    checker = _build_format_checker()
    validators: dict[str, Draft202012Validator] = {}
    for branch in schema.get("oneOf", []):
        kind_const = branch.get("properties", {}).get("kind", {}).get("const")
        if isinstance(kind_const, str):
            validators[kind_const] = Draft202012Validator(branch, format_checker=checker)
    return validators


@lru_cache(maxsize=1)
def _root_validator() -> Draft202012Validator:
    return Draft202012Validator(load_record_schema(), format_checker=_build_format_checker())


def validate_record_schema(record: Any) -> list[SchemaError]:
    """Validate a single record dict against the v0.1-draft JSON Schema.

    Explicitly rejects unknown schema versions and unknown record kinds
    instead of silently falling back or ignoring them.
    """
    if not isinstance(record, dict):
        return [
            SchemaError(
                code="INVALID_RECORD_TYPE",
                message=f"Record must be a JSON object, got {type(record).__name__}.",
            )
        ]

    rec_id = record.get("record_id") if isinstance(record.get("record_id"), str) else None

    schema_version = record.get("schema_version")
    if schema_version is None:
        return [
            SchemaError(
                code="MISSING_SCHEMA_VERSION",
                message="Record is missing required 'schema_version' field.",
                record_id=rec_id,
                path="schema_version",
            )
        ]
    if schema_version != SCHEMA_VERSION:
        return [
            SchemaError(
                code="UNSUPPORTED_SCHEMA_VERSION",
                message=(
                    f"Unsupported schema_version {schema_version!r}; "
                    f"this engine supports only {SCHEMA_VERSION!r}."
                ),
                record_id=rec_id,
                path="schema_version",
                details={"found": schema_version, "supported": SCHEMA_VERSION},
            )
        ]

    kind = record.get("kind")
    if not isinstance(kind, str) or kind not in ALL_RECORD_KINDS:
        return [
            SchemaError(
                code="UNKNOWN_RECORD_KIND",
                message=(
                    f"Unknown or missing record kind {kind!r}; "
                    f"supported kinds: {sorted(ALL_RECORD_KINDS)}."
                ),
                record_id=rec_id,
                path="kind",
                details={"found": kind},
            )
        ]

    validator = _validators_by_kind().get(kind) or _root_validator()
    errors: list[SchemaError] = []
    for err in sorted(validator.iter_errors(record), key=lambda e: list(e.absolute_path)):
        path = ".".join(str(p) for p in err.absolute_path) or "$"
        errors.append(
            SchemaError(
                code="SCHEMA_VIOLATION",
                message=err.message,
                record_id=rec_id,
                path=path,
            )
        )
    return errors
