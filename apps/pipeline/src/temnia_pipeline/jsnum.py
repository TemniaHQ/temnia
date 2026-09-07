"""JavaScript number semantics, for the ports that must match TypeScript byte for byte.

The substrate renderings and the eval issue strings were both built by
TypeScript first, and the parity gates compare the strings they produce, not
what they mean. Python's own formatting and parsing disagree with JavaScript's
at the margins, so the two operations the ports need are spelled out here:

- `to_fixed` is `Number.prototype.toFixed`, which rounds half away from zero on
  the double's true binary value. Python's `format(x, ".2f")` rounds half to
  even and disagrees on every tie.
- `js_number` is `Number(string)`, which the substrate's speaker tag runs on a
  provider's speaker id. It accepts things `int()` refuses ("1.0", "0x10", "",
  " 2 ") and returns NaN rather than raising on the rest.
"""

from __future__ import annotations

import math
import re
from decimal import ROUND_FLOOR, Decimal

_HALF = Decimal("0.5")


def to_fixed(value: float, digits: int) -> str:
    """Format `value` exactly like JavaScript's `value.toFixed(digits)`.

    Operate on the double's true binary value, negate first, and round half up
    on the magnitude (ECMA-262 Number.prototype.toFixed).
    """
    sign = "-" if value < 0 else ""
    scaled = Decimal(abs(value)).scaleb(digits)
    magnitude = int((scaled + _HALF).to_integral_value(rounding=ROUND_FLOOR))
    if digits == 0:
        return f"{sign}{magnitude}"
    text = str(magnitude).zfill(digits + 1)
    return f"{sign}{text[:-digits]}.{text[-digits:]}"


# ECMA-262 StrWhiteSpace: the code points `Number("  1  ")` trims. It is not
# Python's `str.strip()` set, which also trims \x1c-\x1f and does not trim the
# byte order mark.
_JS_WHITESPACE = (
    "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
    "\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)

_DECIMAL = re.compile(r"\A[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")
_RADIX = {"0x": 16, "0X": 16, "0o": 8, "0O": 8, "0b": 2, "0B": 2}
_RADIX_DIGITS = {16: "0123456789abcdefABCDEF", 8: "01234567", 2: "01"}


def js_number(text: str) -> float:
    """`Number(text)` for a string: the value, or NaN where JavaScript gives NaN.

    Follows ECMA-262 StringNumericLiteral: whitespace is trimmed, the empty
    string is 0, `Infinity` is accepted with a sign, `0x`/`0o`/`0b` prefixes are
    unsigned integer literals, and anything else that is not a decimal literal
    is NaN.
    """
    trimmed = text.strip(_JS_WHITESPACE)
    if trimmed == "":
        return 0.0
    if trimmed in {"Infinity", "+Infinity"}:
        return math.inf
    if trimmed == "-Infinity":
        return -math.inf
    radix = _RADIX.get(trimmed[:2])
    if radix is not None:
        digits = trimmed[2:]
        if digits and all(digit in _RADIX_DIGITS[radix] for digit in digits):
            return float(int(digits, radix))
        return math.nan
    if _DECIMAL.match(trimmed):
        return float(trimmed)
    return math.nan


def is_integer(value: float) -> bool:
    """`Number.isInteger(value)`: finite, and equal to its own truncation."""
    return math.isfinite(value) and value == math.trunc(value)


def js_round(value: float) -> int:
    """`Math.round(value)`: floor of value + 0.5, so a tie goes up, not to even."""
    return math.floor(value + 0.5)
