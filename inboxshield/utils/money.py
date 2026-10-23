"""Deterministic money parsing.

Amounts are stored twice: as a validated decimal string (``amount``) for
display/export and as integer minor units (``amount_minor``, 1/100) for exact
comparison.  Floating point equality is never used for financial matching.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_CURRENCY_PREFIX = re.compile(
    r"^(?:GHS|USD|EUR|GBP|NGN|ZAR|KES|XOF|CAD|AUD|INR|CNY|JPY|CHF|AED|[$€£¥])\s*",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")


def parse_amount(value: object, *, allow_zero: bool = False) -> tuple[Decimal, int]:
    """Parse a model-returned amount into (decimal with 2dp, minor units).

    Raises ``ValueError`` for empty, non-numeric, negative, or over-precise
    values.  A zero total is not a plausible invoice total, but a zero tax line
    is valid, so callers can opt in with ``allow_zero``.
    """
    if value is None:
        raise ValueError("amount is missing")
    text = str(value)
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        raise ValueError("amount is missing")

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()
    text = _CURRENCY_PREFIX.sub("", text)
    text = text.replace(",", "").replace("\u00a0", " ").strip()
    text = re.sub(r"(?i)\s*(?:ghs|usd|eur|gbp|ngn|zar|kes|xof|cad|aud|inr|cny|jpy|chf|aed)$", "", text)
    text = text.strip()

    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        raise ValueError(f"amount is not a valid number: {value!r}")
    if negative:
        raise ValueError("invoice totals cannot be negative")

    try:
        decimal_value = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ValueError(f"amount is not a valid number: {value!r}") from exc

    if decimal_value.as_tuple().exponent < -2:
        raise ValueError(f"amount has more than two decimal places: {value!r}")

    quantized = decimal_value.quantize(Decimal("0.01"))
    minor = int(quantized * 100)
    if minor < 0 or (minor == 0 and not allow_zero):
        raise ValueError("invoice total must be greater than zero")
    if minor > 10**13:
        raise ValueError("invoice total is implausibly large")
    return quantized, minor


def format_minor(minor: int | None) -> str | None:
    if minor is None:
        return None
    return f"{Decimal(minor) / Decimal(100):.2f}"


def amounts_match(
    left_minor: int | None,
    right_minor: int | None,
    tolerance_minor: int = 0,
) -> bool:
    if left_minor is None or right_minor is None:
        return False
    return abs(left_minor - right_minor) <= tolerance_minor


def parse_amount_optional(
    value: object, *, allow_zero: bool = False
) -> tuple[Decimal | None, int | None, str | None]:
    """Parse an optional amount, returning (decimal, minor, issue)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None, None
    try:
        decimal_value, minor = parse_amount(value, allow_zero=allow_zero)
        return decimal_value, minor, None
    except ValueError as exc:
        return None, None, str(exc)
