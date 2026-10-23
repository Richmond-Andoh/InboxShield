"""Validation helpers built on top of the normalization schema."""

from __future__ import annotations

from inboxshield.models import ExtractionOutcome
from inboxshield.schemas import normalize_date, normalize_extraction

__all__ = [
    "critical_fields_missing",
    "normalize_date",
    "normalize_extraction",
    "review_reasons",
    "should_store_invoice",
]


def critical_fields_missing(outcome: ExtractionOutcome) -> bool:
    return not outcome.has_required_fields


def should_store_invoice(outcome: ExtractionOutcome) -> bool:
    """Store a record when any meaningful invoice data was extracted.

    Malformed model output (``success=False``) is never stored as an invoice;
    the processing job is marked failed instead.
    """
    if not outcome.success:
        return False
    return any(
        [
            outcome.vendor,
            outcome.amount_minor is not None,
            outcome.currency,
            outcome.invoice_number,
            outcome.invoice_date,
        ]
    )


def review_reasons(outcome: ExtractionOutcome) -> list[str]:
    reasons: list[str] = []
    if not outcome.vendor:
        reasons.append("Vendor could not be extracted")
    if outcome.amount_minor is None:
        reasons.append("Total amount could not be validated")
    if not outcome.currency:
        reasons.append("Currency could not be identified")
    reasons.extend(outcome.issues)
    return list(dict.fromkeys(reasons))
