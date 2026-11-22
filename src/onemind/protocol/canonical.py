"""RFC 8785 JSON Canonicalization Scheme (JCS) and SHA-256 digest utilities."""

from __future__ import annotations

import hashlib
import math
from typing import Any


def _escape_jcs_string(value: str) -> str:
    """Serialize a Python string per RFC 8785 Section 3.2.2.2."""
    out: list[str] = ['"']
    for ch in value:
        code = ord(ch)
        if 0xD800 <= code <= 0xDFFF:
            raise ValueError(f"Lone surrogate U+{code:04X} cannot be canonicalized in UTF-8 JSON")
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif code == 0x08:
            out.append("\\b")
        elif code == 0x09:
            out.append("\\t")
        elif code == 0x0A:
            out.append("\\n")
        elif code == 0x0C:
            out.append("\\f")
        elif code == 0x0D:
            out.append("\\r")
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _format_es6_number(value: float) -> str:
    """Format an IEEE 754 double according to ECMA-262 / RFC 8785 Section 3.2.2.3."""
    if math.isnan(value) or math.isinf(value):
        raise ValueError(f"Non-finite IEEE 754 number cannot be canonicalized: {value!r}")
    if value == 0.0:
        return "0"

    sign = "-" if value < 0 else ""
    abs_val = abs(value)
    # Python 3 repr(float) uses David Gay's shortest round-trip decimal algorithm (same as ES6).
    raw = repr(abs_val)

    if "e" in raw or "E" in raw:
        mantissa_str, exp_str = raw.lower().split("e")
        exp = int(exp_str)
    else:
        mantissa_str = raw
        exp = 0

    if "." in mantissa_str:
        int_part, frac_part = mantissa_str.split(".")
        digits = int_part + frac_part
        decimal_pos = len(int_part) + exp
    else:
        digits = mantissa_str
        decimal_pos = len(mantissa_str) + exp

    # Strip leading zeros and adjust decimal position
    leading = len(digits) - len(digits.lstrip("0"))
    digits = digits[leading:]
    decimal_pos -= leading
    # Strip trailing zeros
    digits = digits.rstrip("0")

    k = len(digits)
    n = decimal_pos

    if k <= n <= 21:
        formatted = digits + ("0" * (n - k))
    elif 0 < n <= 21:
        formatted = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        formatted = "0." + ("0" * (-n)) + digits
    elif k == 1:
        e_sign = "+" if (n - 1) >= 0 else "-"
        formatted = f"{digits}e{e_sign}{abs(n - 1)}"
    else:
        e_sign = "+" if (n - 1) >= 0 else "-"
        formatted = f"{digits[0]}.{digits[1:]}e{e_sign}{abs(n - 1)}"

    return sign + formatted


def _serialize_jcs(value: Any) -> str:
    """Recursively serialize a JSON-compatible Python value per RFC 8785."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        # Integers outside IEEE 754 exact double range still serialize accurately as integers
        # if within safe JSON integer conventions, or via float if needed; in practice RFC 8785
        # preserves exact integers up to 2^53-1 identically to str(value).
        return str(value)
    if isinstance(value, float):
        return _format_es6_number(value)
    if isinstance(value, str):
        return _escape_jcs_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_serialize_jcs(item) for item in value) + "]"
    if isinstance(value, dict):
        items: list[tuple[str, Any]] = []
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"JSON object keys must be strings, got {type(k).__name__}")
            items.append((k, v))
        # RFC 8785 Section 3.2.3: sort property names by UTF-16 code units.
        items.sort(key=lambda kv: kv[0].encode("utf-16-be"))
        pairs = (f"{_escape_jcs_string(k)}:{_serialize_jcs(v)}" for k, v in items)
        return "{" + ",".join(pairs) + "}"
    raise TypeError(f"Unsupported type for RFC 8785 canonicalization: {type(value).__name__}")


def canonicalize_json(value: Any) -> str:
    """Return the RFC 8785 canonical JSON string representation of value."""
    return _serialize_jcs(value)


def canonical_bytes(value: Any) -> bytes:
    """Return the UTF-8 encoded RFC 8785 canonical JSON bytes of value."""
    return canonicalize_json(value).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the lowercase hex SHA-256 digest of the RFC 8785 canonical JSON of value."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def excerpt_sha256(content: str | bytes) -> str:
    """Return the lowercase hex SHA-256 digest of a source excerpt."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()
