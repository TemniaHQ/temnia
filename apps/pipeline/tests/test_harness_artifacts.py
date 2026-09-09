from __future__ import annotations

import math

import pytest

from temnia_pipeline.harness.artifacts import canonical_json, fingerprint_for


def test_canonical_json_is_stable_utf8_and_compact() -> None:
    left = canonical_json({"z": [2, 1], "label": "Témnia"})
    right = canonical_json({"label": "Témnia", "z": [2, 1]})
    assert left == right == b'{"label":"T\xc3\xa9mnia","z":[2,1]}'


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_canonical_json_refuses_nonfinite_numbers(value: float) -> None:
    with pytest.raises(ValueError, match="finite canonical JSON"):
        canonical_json({"score": value})


def test_input_fingerprint_is_separate_from_content() -> None:
    first = fingerprint_for(kind="proposal", inputs={"evidence": "abc"}, config={"seat": 1})
    reordered = fingerprint_for(kind="proposal", config={"seat": 1}, inputs={"evidence": "abc"})
    changed = fingerprint_for(kind="proposal", inputs={"evidence": "def"}, config={"seat": 1})
    assert first == reordered
    assert first != changed
    assert len(first) == 64
