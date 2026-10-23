"""Text normalization helpers used by extraction and duplicate matching."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_COMPANY_SUFFIXES = {
    "ltd",
    "limited",
    "llc",
    "llp",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "plc",
    "gmbh",
    "bv",
    "nv",
    "pty",
    "sarl",
    "sa",
    "ag",
    "enterprise",
    "enterprises",
    "trading",
    "group",
}

_PUNCTUATION = re.compile(r"[^\w\s&]")
_WHITESPACE = re.compile(r"\s+")


def strip_accents(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )


def normalize_vendor(value: str | None) -> str:
    """Normalize a vendor name for comparison (not for display)."""
    if not value:
        return ""
    text = strip_accents(value).lower().replace("&", " and ")
    text = _PUNCTUATION.sub(" ", text)
    tokens = [token for token in _WHITESPACE.split(text) if token]
    while tokens and tokens[-1] in _COMPANY_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def normalize_invoice_number(value: str | None) -> str:
    if not value:
        return ""
    text = strip_accents(value).upper()
    return re.sub(r"[^A-Z0-9]", "", text)


def _singularize_token(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def vendor_similarity(left: str | None, right: str | None) -> float:
    """Blend token containment with character similarity, in [0, 1]."""
    a = normalize_vendor(left)
    b = normalize_vendor(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    tokens_a = {_singularize_token(token) for token in a.split()}
    tokens_b = {_singularize_token(token) for token in b.split()}
    union = tokens_a | tokens_b
    jaccard = len(tokens_a & tokens_b) / len(union) if union else 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    containment = (
        len(tokens_a & tokens_b) / min(len(tokens_a), len(tokens_b))
        if tokens_a and tokens_b
        else 0.0
    )
    return max(ratio * 0.6 + jaccard * 0.4, containment * 0.9)


def shorten(value: str, limit: int = 120) -> str:
    value = _WHITESPACE.sub(" ", value).strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."
