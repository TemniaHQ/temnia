"""The JavaScript number shims must match JavaScript exactly.

`to_fixed` and its first four cases come from the legacy A1 port; the rest cover
`Number(string)` and `Math.round`, which the substrate's speaker tag and shot
grid run on values that came out of TypeScript.
"""

import math

from temnia_pipeline.jsnum import is_integer, js_number, js_round, to_fixed


def test_to_fixed_rounds_half_away_from_zero() -> None:
    # Python's format() would give "2" and "-2" here (banker's rounding).
    assert to_fixed(2.5, 0) == "3"
    assert to_fixed(-2.5, 0) == "-3"
    assert to_fixed(0.125, 2) == "0.13"


def test_to_fixed_operates_on_the_binary_double() -> None:
    # 1.005 is really 1.00499999999999989..., so JS gives "1.00", not "1.01".
    assert to_fixed(1.005, 2) == "1.00"
    # 0.5 is exact, so the tie rounds up.
    assert to_fixed(0.5, 0) == "1"


def test_to_fixed_pads_and_carries() -> None:
    assert to_fixed(0.07, 1) == "0.1"
    assert to_fixed(9.96, 1) == "10.0"
    assert to_fixed(100.0, 0) == "100"
    assert to_fixed(0.0, 1) == "0.0"


def test_to_fixed_renders_the_pause_glyph_the_way_the_grid_does() -> None:
    assert to_fixed(701 / 1000, 1) == "0.7"
    assert to_fixed(2600 / 1000, 1) == "2.6"
    assert to_fixed(1500 / 1000, 1) == "1.5"


def test_js_number_reads_what_int_refuses() -> None:
    assert js_number("0") == 0
    assert js_number("12") == 12
    assert js_number("1.0") == 1
    assert js_number("1.5") == 1.5
    assert js_number("1e3") == 1000
    assert js_number("+7") == 7
    assert js_number("0x10") == 16
    assert js_number("0b101") == 5
    assert js_number("0o17") == 15
    assert js_number("Infinity") == math.inf
    assert js_number("-Infinity") == -math.inf


def test_js_number_treats_blank_as_zero_and_trims_js_whitespace() -> None:
    assert js_number("") == 0
    assert js_number("   ") == 0
    assert js_number(" 2 ") == 2
    assert js_number("﻿3") == 3


def test_js_number_is_nan_where_javascript_is_nan() -> None:
    assert math.isnan(js_number("guest"))
    assert math.isnan(js_number("1,000"))
    assert math.isnan(js_number("0x"))
    assert math.isnan(js_number("-0x10"))
    assert math.isnan(js_number("1 2"))


def test_is_integer_matches_number_is_integer() -> None:
    assert is_integer(2.0)
    assert is_integer(-0.0)
    assert not is_integer(2.5)
    assert not is_integer(math.nan)
    assert not is_integer(math.inf)


def test_js_round_sends_a_tie_up_not_to_even() -> None:
    # Python's round() would give 2 and 4 for these.
    assert js_round(2.5) == 3
    assert js_round(3.5) == 4
    assert js_round(-2.5) == -2
    assert js_round(2.0005 * 1000) == 2001
