"""Explainable duplicate detection.

The engine produces investigation *signals*.  A signal is never proof of fraud,
and only a strong vendor + invoice-number + amount + currency match escalates an
invoice to ``FLAGGED_DUPLICATE``.  Vendor/amount-only matches are routed to
review.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from inboxshield.config import Settings
from inboxshield.models import FindingDraft, FindingType, InvoiceCandidate, RiskLevel
from inboxshield.utils.money import amounts_match
from inboxshield.utils.text import vendor_similarity

logger = logging.getLogger(__name__)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()  # noqa: DTZ007
    except ValueError:
        return None


def _days_between(left: str | None, right: str | None) -> int | None:
    a, b = _parse_date(left), _parse_date(right)
    if a is None or b is None:
        return None
    return abs((a - b).days)


def _same_vendor(candidate: InvoiceCandidate, other: InvoiceCandidate) -> bool:
    return bool(candidate.vendor_normalized) and (
        candidate.vendor_normalized == other.vendor_normalized
    )


def _same_amount(
    candidate: InvoiceCandidate, other: InvoiceCandidate, tolerance: int
) -> bool:
    return amounts_match(candidate.amount_minor, other.amount_minor, tolerance)


def _same_number(candidate: InvoiceCandidate, other: InvoiceCandidate) -> bool:
    return bool(candidate.invoice_number_normalized) and (
        candidate.invoice_number_normalized == other.invoice_number_normalized
    )


def _currency_label(candidate: InvoiceCandidate, other: InvoiceCandidate) -> str:
    return f"{candidate.currency or '?'} vs {other.currency or '?'}"


def detect_duplicates(
    candidate: InvoiceCandidate,
    existing: list[InvoiceCandidate],
    settings: Settings,
) -> list[FindingDraft]:
    """Compare one incoming invoice against stored invoices."""
    findings: list[FindingDraft] = []
    seen: set[tuple[str, int | None]] = set()

    def add(finding: FindingDraft) -> None:
        key = (finding.finding_type.value, finding.matched_invoice_id)
        if key not in seen:
            seen.add(key)
            findings.append(finding)

    for other in existing:
        if other.id is None or other.id == candidate.id:
            continue
        same_number = _same_number(candidate, other)
        same_vendor = _same_vendor(candidate, other)
        same_amount = _same_amount(
            candidate, other, settings.amount_match_tolerance_minor
        )
        same_currency = bool(candidate.currency) and candidate.currency == other.currency
        days = _days_between(candidate.invoice_date, other.invoice_date)
        within_window = days is not None and days <= settings.duplicate_date_window_days
        date_unknown = days is None

        if same_number and same_vendor:
            if same_amount and same_currency:
                add(
                    FindingDraft(
                        finding_type=FindingType.STRONG_INVOICE_DUPLICATE,
                        risk_level=RiskLevel.HIGH,
                        explanation=(
                            f"Strong duplicate candidate: vendor and invoice number "
                            f"{candidate.invoice_number!r} match invoice #{other.id}, "
                            f"and the total ({candidate.currency} "
                            f"{_fmt(candidate.amount_minor)}) also matches. Review "
                            "before any payment action."
                        ),
                        matched_invoice_id=other.id,
                    )
                )
            else:
                add(
                    FindingDraft(
                        finding_type=FindingType.INVOICE_NUMBER_CONFLICT,
                        risk_level=RiskLevel.MEDIUM,
                        explanation=(
                            f"Review required: invoice number "
                            f"{candidate.invoice_number!r} matches invoice #{other.id}, "
                            f"but the total or currency differs "
                            f"({_currency_label(candidate, other)}; "
                            f"{_fmt(candidate.amount_minor)} vs "
                            f"{_fmt(other.amount_minor)})."
                        ),
                        matched_invoice_id=other.id,
                    )
                )
            continue

        if same_number and not same_vendor:
            add(
                FindingDraft(
                    finding_type=FindingType.INVOICE_NUMBER_CONFLICT,
                    risk_level=RiskLevel.MEDIUM,
                    explanation=(
                        f"Review required: invoice number "
                        f"{candidate.invoice_number!r} matches invoice #{other.id}, "
                        "but the vendor names differ."
                    ),
                    matched_invoice_id=other.id,
                )
            )
            continue

        if same_amount and same_currency and same_vendor:
            if within_window or date_unknown:
                if (
                    candidate.invoice_number_normalized
                    and other.invoice_number_normalized
                    and candidate.invoice_number_normalized
                    != other.invoice_number_normalized
                ):
                    number_note = (
                        f"Invoice numbers differ ({candidate.invoice_number!r} vs "
                        f"{other.invoice_number!r})."
                    )
                elif not candidate.invoice_number_normalized:
                    number_note = "The new invoice has no readable invoice number."
                else:
                    number_note = "The invoice number is missing on the stored record."
                date_note = (
                    f"Issue dates are {days} day(s) apart."
                    if days is not None
                    else "One or both issue dates are unavailable."
                )
                add(
                    FindingDraft(
                        finding_type=FindingType.POTENTIAL_DUPLICATE,
                        risk_level=RiskLevel.MEDIUM,
                        explanation=(
                            f"Possible duplicate: vendor, currency, and total amount "
                            f"({candidate.currency} {_fmt(candidate.amount_minor)}) "
                            f"match invoice #{other.id}. {number_note} {date_note} "
                            "This is a review signal, not proof of fraud."
                        ),
                        matched_invoice_id=other.id,
                    )
                )
            continue

        if same_amount and same_currency and not same_vendor:
            similarity = vendor_similarity(
                candidate.vendor_normalized, other.vendor_normalized
            )
            if similarity >= settings.vendor_similarity_threshold and (
                within_window or date_unknown
            ):
                add(
                    FindingDraft(
                        finding_type=FindingType.SIMILAR_VENDOR_MATCH,
                        risk_level=RiskLevel.MEDIUM,
                        explanation=(
                            f"Similar vendor name ({similarity:.0%} similarity) and "
                            f"identical amount ({candidate.currency} "
                            f"{_fmt(candidate.amount_minor)}) as invoice #{other.id}. "
                            f"Vendor names: {candidate.vendor!r} vs {other.vendor!r}."
                        ),
                        matched_invoice_id=other.id,
                    )
                )

    findings.sort(key=lambda item: (item.risk_level != RiskLevel.HIGH, item.finding_type.value))
    return findings


def _fmt(minor: int | None) -> str:
    return f"{minor / 100:.2f}" if minor is not None else "unknown"
