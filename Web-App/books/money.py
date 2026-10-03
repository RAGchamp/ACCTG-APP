"""Money as integer cents (plan P5). No floats anywhere in the books.

parse_cents() reads amounts the way the analyzer's ocr_quality.parse_amount()
does ("(332.19)" is negative, "$ 1,204.50", a lone dash is nil, [?] is
unknown), but returns exact integer cents via Decimal.
"""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


class MoneyError(ValueError):
    pass


def parse_cents(value, allow_none=True):
    """"$1,204.50" -> 120450; "(12.00)" -> -1200; "-" -> 0; 12.5 -> 1250.

    Returns None for "", None, words or a figure with [?] (unknown), or raises
    MoneyError when allow_none is False."""
    if value is None:
        if allow_none:
            return None
        raise MoneyError("amount missing")
    if isinstance(value, bool):
        raise MoneyError(f"not an amount: {value!r}")
    if isinstance(value, int):
        return value * 100
    if isinstance(value, float):
        value = repr(value)
    text = str(value).replace("*", "").strip()
    if re.fullmatch(r"\$?\s*[-–—]{1,2}", text):
        return 0
    if not text or "[?]" in text or not re.search(r"\d", text):
        if allow_none:
            return None
        raise MoneyError(f"not an amount: {value!r}")
    negative = (text.startswith("(") and text.endswith(")")) or text.startswith("-") \
        or text.endswith("-") or text.upper().endswith("CR")
    core = re.sub(r"(?i)cr$", "", text)
    core = re.sub(r"[()$\s+\-]", "", core).replace(",", "")
    try:
        amount = Decimal(core)
    except InvalidOperation:
        if allow_none:
            return None
        raise MoneyError(f"not an amount: {value!r}")
    cents = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return -cents if negative else cents


def to_decimal_str(cents):
    """120450 -> "1204.50" (the form Claude and JSON files use)."""
    if cents is None:
        return None
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def fmt(cents, dollar=True, blank_zero=False, parens=True):
    """120450 -> "$1,204.50"; -1200 -> "($12.00)"."""
    if cents is None:
        return ""
    if blank_zero and cents == 0:
        return ""
    neg = cents < 0
    cents = abs(cents)
    text = f"{cents // 100:,}.{cents % 100:02d}"
    if dollar:
        text = "$" + text
    if neg:
        text = f"({text})" if parens else "-" + text
    return text


def pct_of(cents, rate_percent):
    """Tax on an amount, rounded half up to the cent: pct_of(10000, "7.35") -> 735."""
    value = Decimal(cents) * Decimal(str(rate_percent)) / Decimal(100)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def allocate(total_cents, weights):
    """Split total_cents in proportion to weights (ints); the rounding remainder
    goes to the largest weight, so the parts always add up exactly."""
    if not weights:
        return []
    wsum = sum(weights)
    if wsum == 0:
        parts = [0] * len(weights)
        parts[0] = total_cents
        return parts
    parts = [total_cents * w // wsum for w in weights]
    biggest = max(range(len(weights)), key=lambda i: weights[i])
    parts[biggest] += total_cents - sum(parts)
    return parts
