"""Tests for RFC 8785 JSON Canonicalization Scheme (JCS), conformance vectors, and digest binding."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from onemind.protocol.canonical import (
    MAX_SAFE_IEEE754_INT,
    CanonicalizationError,
    canonical_sha256,
    canonicalize_json,
    excerpt_sha256,
)
from onemind.protocol.validator import validate_records


def test_jcs_rfc8785_key_sorting_utf16() -> None:
    """RFC 8785 Section 3.2.3 sorting test vector (UTF-16 code unit order)."""
    # U+1F600 (UTF-16: D83D DE00) and U+1D11E (D834 DD1E) MUST sort BEFORE
    # U+E000 (E000), U+FB33 (FB33), and U+FFFF (FFFF) in UTF-16 code-unit order.
    sample = {
        "\uffff": "Max BMP",
        "\u20ac": "Euro Sign",
        "\r": "Carriage Return",
        "1": "One",
        "\u0080": "Control",
        "\ufb33": "Hebrew Letter Dalet With Dagesh",
        "\ue000": "Private Use",
        "\U0001f600": "Emoji: Grinning Face",
        "\U0001d11e": "Musical Symbol G Clef",
        "\u00f6": "Latin Small Letter O With Diaeresis",
    }
    canon = canonicalize_json(sample)
    expected = (
        '{"\\r":"Carriage Return","1":"One","\u0080":"Control",'
        '"\u00f6":"Latin Small Letter O With Diaeresis","\u20ac":"Euro Sign",'
        '"\U0001d11e":"Musical Symbol G Clef","\U0001f600":"Emoji: Grinning Face",'
        '"\ue000":"Private Use","\ufb33":"Hebrew Letter Dalet With Dagesh","\uffff":"Max BMP"}'
    )
    assert canon == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # RFC 8785 Section 3.2.2.3 / Appendix B IEEE 754 binary64 conformance vectors
        (0, "0"),
        (0.0, "0"),
        (-0.0, "0"),
        (1.0, "1"),
        (-1.0, "-1"),
        (5e-324, "5e-324"),  # smallest positive subnormal double
        (2.225073858507201e-308, "2.225073858507201e-308"),
        (1.7976931348623157e308, "1.7976931348623157e+308"),  # max finite double
        (1e-27, "1e-27"),
        (1e-7, "1e-7"),
        (1e-6, "0.000001"),
        (9.999999999999997e-7, "9.999999999999997e-7"),
        (0.002, "0.002"),
        (4.50, "4.5"),
        (333333333.33333329, "333333333.3333333"),
        (1e20, "100000000000000000000"),
        (999999999999999900000.0, "999999999999999900000"),
        (1e21, "1e+21"),
        (1e23, "1e+23"),
        (1e30, "1e+30"),
        (MAX_SAFE_IEEE754_INT, "9007199254740991"),
        (-MAX_SAFE_IEEE754_INT, "-9007199254740991"),
    ],
)
def test_jcs_rfc8785_numeric_conformance_vectors(value: int | float, expected: str) -> None:
    assert canonicalize_json(value) == expected


def test_jcs_float_rejection_fallback_and_unsafe_int_rejection() -> None:
    # Default rejects NaN, Infinity, and integers outside IEEE 754 safe range
    with pytest.raises(CanonicalizationError):
        canonicalize_json(float("nan"))
    with pytest.raises(CanonicalizationError):
        canonicalize_json(float("inf"))
    with pytest.raises(CanonicalizationError):
        canonicalize_json(float("-inf"))
    with pytest.raises(CanonicalizationError):
        canonicalize_json(MAX_SAFE_IEEE754_INT + 1)
    with pytest.raises(CanonicalizationError):
        canonicalize_json(-(MAX_SAFE_IEEE754_INT + 1))

    # Strict float-rejection fallback rejects floats while permitting safe integers
    assert canonicalize_json({"count": 42, "ok": True}, reject_floats=True) == '{"count":42,"ok":true}'
    with pytest.raises(CanonicalizationError):
        canonicalize_json({"score": 0.95}, reject_floats=True)
    with pytest.raises(CanonicalizationError):
        canonical_sha256({"score": 1.0}, reject_floats=True)

    # Non-string dict keys rejected
    with pytest.raises(TypeError):
        canonicalize_json({1: "one"})


def test_jcs_unicode_escaping_and_surrogate_rejection() -> None:
    # Control chars 0x00..0x1F use short escapes or lowercase \u00xx; solidus '/' NOT escaped; UTF-8 preserved
    s = 'a"b\\c\b\f\n\r\t\x00\x07\x0b\x1f/caf\u00e9-\u4e2d\u6587-\U0001f600'
    assert (
        canonicalize_json(s)
        == '"a\\"b\\\\c\\b\\f\\n\\r\\t\\u0000\\u0007\\u000b\\u001f/caf\u00e9-\u4e2d\u6587-\U0001f600"'
    )
    # Lone surrogates must be rejected
    with pytest.raises(CanonicalizationError):
        canonicalize_json("\ud800")
    with pytest.raises(CanonicalizationError):
        canonicalize_json("\udfff")


def test_jcs_immutable_mapping_and_reference_approval_digest_binding() -> None:
    """Verify conformance against shipped examples/records.json digests and key-order invariance."""
    repo_root = Path(__file__).resolve().parents[1]
    records = json.loads((repo_root / "examples" / "records.json").read_text(encoding="utf-8"))
    by_id = {r["record_id"]: r for r in records}

    assert excerpt_sha256(by_id["fixture-0001"]["body"]["redacted_snapshot"]) == (
        by_id["fixture-0001"]["body"]["excerpt_sha256"]
    )
    pv = by_id["fixture-0003"]
    dec = by_id["fixture-0004"]
    assert canonical_sha256(pv) == dec["body"]["target_sha256"]
    assert canonical_sha256(by_id["fixture-0005"]) == by_id["fixture-0006"]["body"]["target_sha256"]

    # Reordered keys and MappingProxyType produce the exact same digest
    reordered_pv = {k: pv[k] for k in reversed(list(pv.keys()))}
    assert canonical_sha256(reordered_pv) == dec["body"]["target_sha256"]
    assert canonical_sha256(MappingProxyType(reordered_pv)) == dec["body"]["target_sha256"]

    # Mutating a single character in target breaks approval digest binding
    mutated_records = copy.deepcopy(records)
    for r in mutated_records:
        if r["record_id"] == "fixture-0003":
            r["body"]["statement"] += " "
    report = validate_records(mutated_records)
    assert not report.valid
    assert any(i.code == "TARGET_DIGEST_MISMATCH" for i in report.errors)
