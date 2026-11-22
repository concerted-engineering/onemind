"""Tests for RFC 8785 JSON Canonicalization Scheme (JCS) and SHA-256 digests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from onemind.protocol.canonical import (
    canonical_sha256,
    canonicalize_json,
    excerpt_sha256,
)


def test_jcs_rfc8785_key_sorting_utf16() -> None:
    """RFC 8785 Section 3.2.3 sorting test vector (UTF-16 code unit order)."""
    # U+1F600 (surrogate pair D83D DE00) MUST sort BEFORE U+E000 (private use E000)
    # and BEFORE U+FB33 in UTF-16 code units, even though in UTF-8/Unicode code points
    # 0x1F600 > 0xE000!
    sample = {
        "\u20ac": "Euro Sign",
        "\r": "Carriage Return",
        "1": "One",
        "\u0080": "Control",
        "\ufb33": "Hebrew Letter Dalet With Dagesh",
        "\ud83d\ude00".encode("utf-16", "surrogatepass").decode("utf-16"): "Emoji: Grinning Face",
        "\u00f6": "Latin Small Letter O With Diaeresis",
    }
    canon = canonicalize_json(sample)
    expected = (
        '{"\\r":"Carriage Return","1":"One","\u0080":"Control",'
        '"\u00f6":"Latin Small Letter O With Diaeresis","\u20ac":"Euro Sign",'
        '"\U0001f600":"Emoji: Grinning Face","\ufb33":"Hebrew Letter Dalet With Dagesh"}'
    )
    assert canon == expected


def test_jcs_rfc8785_numbers() -> None:
    """RFC 8785 Section 3.2.2.3 ES6 number serialization vectors."""
    assert canonicalize_json(0) == "0"
    assert canonicalize_json(0.0) == "0"
    assert canonicalize_json(-0.0) == "0"
    assert canonicalize_json(1.0) == "1"
    assert canonicalize_json(1e20) == "100000000000000000000"
    assert canonicalize_json(1e21) == "1e+21"
    assert canonicalize_json(1e-6) == "0.000001"
    assert canonicalize_json(1e-7) == "1e-7"
    assert canonicalize_json(333333333.33333329) == "333333333.3333333"
    assert canonicalize_json(1e30) == "1e+30"
    assert canonicalize_json(4.50) == "4.5"
    assert canonicalize_json(2e-3) == "0.002"
    assert canonicalize_json(0.000000000000000000000000001) == "1e-27"


def test_jcs_rejects_nan_infinity_and_non_string_keys() -> None:
    with pytest.raises(ValueError):
        canonicalize_json(float("nan"))
    with pytest.raises(ValueError):
        canonicalize_json(float("inf"))
    with pytest.raises(ValueError):
        canonicalize_json(float("-inf"))
    with pytest.raises(TypeError):
        canonicalize_json({1: "one"})


def test_jcs_strings_and_control_chars() -> None:
    assert canonicalize_json('a"b\\c\b\f\n\r\t\x00\x1f/') == '"a\\"b\\\\c\\b\\f\\n\\r\\t\\u0000\\u001f/"'


def test_jcs_matches_examples_records_json_digests() -> None:
    """Verify conformance against shipped examples/records.json digests."""
    repo_root = Path(__file__).resolve().parents[1]
    records = json.loads((repo_root / "examples" / "records.json").read_text(encoding="utf-8"))
    by_id = {r["record_id"]: r for r in records}

    assert excerpt_sha256(by_id["fixture-0001"]["body"]["redacted_snapshot"]) == (
        by_id["fixture-0001"]["body"]["excerpt_sha256"]
    )
    assert canonical_sha256(by_id["fixture-0003"]) == by_id["fixture-0004"]["body"]["target_sha256"]
    assert canonical_sha256(by_id["fixture-0005"]) == by_id["fixture-0006"]["body"]["target_sha256"]
