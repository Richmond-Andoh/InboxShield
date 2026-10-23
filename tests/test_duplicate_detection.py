"""Duplicate detection and audit-status behavior."""

from __future__ import annotations

from dataclasses import replace

from conftest import valid_outcome

from inboxshield.audit.duplicate_detector import detect_duplicates
from inboxshield.audit.risk_engine import determine_status, quality_findings
from inboxshield.config import Settings
from inboxshield.models import (
    FindingType,
    InvoiceCandidate,
    InvoiceStatus,
    RiskLevel,
)
from inboxshield.utils.text import normalize_invoice_number, normalize_vendor


def candidate(
    *,
    id: int | None = None,
    vendor: str = "Acme Supplies Ltd",
    number: str | None = "INV-001",
    amount: int | None = 125000,
    currency: str | None = "GHS",
    when: str | None = "2026-10-20",
) -> InvoiceCandidate:
    return InvoiceCandidate(
        id=id,
        vendor=vendor,
        vendor_normalized=normalize_vendor(vendor),
        invoice_number=number,
        invoice_number_normalized=normalize_invoice_number(number),
        amount_minor=amount,
        currency=currency,
        invoice_date=when,
    )


def test_identical_hash_is_not_part_of_amount_matching() -> None:
    # Exact file hashes are handled before the audit engine; this test documents
    # that duplicate signals here are financial, not byte-level.
    assert FindingType.EXACT_FILE_DUPLICATE.value == "EXACT_FILE_DUPLICATE"


def test_strong_duplicate_requires_number_vendor_amount_currency(settings: Settings) -> None:
    new = candidate(number="INV-001")
    existing = candidate(id=7)
    findings = detect_duplicates(new, [existing], settings)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.finding_type == FindingType.STRONG_INVOICE_DUPLICATE
    assert finding.risk_level == RiskLevel.HIGH
    assert finding.matched_invoice_id == 7
    assert "invoice #7" in finding.explanation


def test_matching_number_with_different_total_is_review_not_duplicate(
    settings: Settings,
) -> None:
    findings = detect_duplicates(
        candidate(number="INV-001", amount=142000), [candidate(id=3)], settings
    )
    assert findings[0].finding_type == FindingType.INVOICE_NUMBER_CONFLICT
    assert findings[0].risk_level == RiskLevel.MEDIUM


def test_matching_number_with_different_vendor_is_review(settings: Settings) -> None:
    findings = detect_duplicates(
        candidate(vendor="Other Vendor Ltd"), [candidate(id=3)], settings
    )
    assert findings[0].finding_type == FindingType.INVOICE_NUMBER_CONFLICT
    assert findings[0].risk_level == RiskLevel.MEDIUM


def test_same_vendor_amount_currency_is_potential_duplicate(settings: Settings) -> None:
    findings = detect_duplicates(
        candidate(number="INV-002", when="2026-10-21"), [candidate(id=5)], settings
    )
    finding = findings[0]
    assert finding.finding_type == FindingType.POTENTIAL_DUPLICATE
    assert finding.risk_level == RiskLevel.MEDIUM
    assert "not proof of fraud" in finding.explanation
    assert "Invoice numbers differ" in finding.explanation


def test_vendor_amount_match_never_escalates_to_duplicate(settings: Settings) -> None:
    findings = detect_duplicates(
        candidate(number=None, when=None), [candidate(id=5, number="INV-999")], settings
    )
    assert all(
        finding.finding_type != FindingType.STRONG_INVOICE_DUPLICATE
        for finding in findings
    )
    status = determine_status(valid_outcome(vendor="acme supplies"), findings)
    assert status == InvoiceStatus.FLAGGED_REVIEW


def test_different_currency_same_amount_is_not_a_duplicate(settings: Settings) -> None:
    findings = detect_duplicates(
        candidate(currency="USD", number="INV-999"), [candidate(id=5)], settings
    )
    assert findings == []


def test_amount_match_far_apart_is_not_flagged(settings: Settings) -> None:
    findings = detect_duplicates(
        candidate(number="INV-300", when="2026-01-01"), [candidate(id=5)], settings
    )
    assert findings == []


def test_missing_invoice_dates_still_allow_review(settings: Settings) -> None:
    findings = detect_duplicates(
        candidate(number="INV-300", when=None),
        [candidate(id=5, when=None)],
        settings,
    )
    assert findings[0].finding_type == FindingType.POTENTIAL_DUPLICATE
    assert "dates are unavailable" in findings[0].explanation


def test_similar_vendor_names_are_flagged(settings: Settings) -> None:
    findings = detect_duplicates(
        candidate(vendor="Acme Supply Limited", number="A-2"),
        [candidate(id=9, vendor="Acme Supplies Ltd")],
        settings,
    )
    assert any(
        finding.finding_type == FindingType.SIMILAR_VENDOR_MATCH for finding in findings
    )


def test_multiple_candidates_produce_multiple_findings(settings: Settings) -> None:
    existing = [
        candidate(id=1, number="INV-001"),
        candidate(id=2, number="INV-002", vendor="Acme Supply Ltd"),
        candidate(id=3, vendor="Different Co", number="D-3", amount=5000),
    ]
    findings = detect_duplicates(candidate(number="INV-001"), existing, settings)
    matched = {finding.matched_invoice_id for finding in findings}
    assert matched == {1, 2}
    assert len(findings) == 2


def test_amount_tolerance_is_configurable(settings: Settings) -> None:
    tolerant = replace(settings, amount_match_tolerance_minor=100)
    findings = detect_duplicates(
        candidate(amount=125050, number="INV-X"), [candidate(id=2)], tolerant
    )
    assert findings
    exact_only = detect_duplicates(
        candidate(amount=125050, number="INV-X"), [candidate(id=2)], settings
    )
    assert exact_only == []


def test_no_matches_returns_verified(settings: Settings) -> None:
    findings = detect_duplicates(candidate(), [], settings)
    assert findings == []
    status = determine_status(valid_outcome(), findings)
    assert status == InvoiceStatus.VERIFIED_LOGGED


def test_quality_findings_for_missing_fields(settings: Settings) -> None:
    outcome = valid_outcome(vendor=None, amount_minor=None, amount=None)
    quality = quality_findings(outcome)
    assert quality[0].finding_type == FindingType.INCOMPLETE_EXTRACTION
    assert determine_status(outcome, quality) == InvoiceStatus.FLAGGED_REVIEW


def test_quality_findings_for_consistency_error() -> None:
    outcome = valid_outcome(issues=["Subtotal plus tax does not equal the extracted total"])
    quality = quality_findings(outcome)
    assert quality[0].finding_type == FindingType.INVALID_EXTRACTION
    assert determine_status(outcome, quality) == InvoiceStatus.FLAGGED_REVIEW
