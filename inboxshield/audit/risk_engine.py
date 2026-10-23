"""Turns extraction quality and duplicate signals into an audit status."""

from __future__ import annotations

from inboxshield.ai.validation import review_reasons
from inboxshield.models import (
    ExtractionOutcome,
    FindingDraft,
    FindingType,
    InvoiceStatus,
    RiskLevel,
)

DUPLICATE_TYPES = {
    FindingType.EXACT_FILE_DUPLICATE,
    FindingType.STRONG_INVOICE_DUPLICATE,
    FindingType.POTENTIAL_DUPLICATE,
    FindingType.SIMILAR_VENDOR_MATCH,
    FindingType.INVOICE_NUMBER_CONFLICT,
}


def is_duplicate_finding(finding: FindingDraft) -> bool:
    return finding.finding_type in DUPLICATE_TYPES


def quality_findings(outcome: ExtractionOutcome) -> list[FindingDraft]:
    if not outcome.success:
        return []
    reasons = review_reasons(outcome)
    if not reasons:
        return []
    if not outcome.has_required_fields:
        return [
            FindingDraft(
                finding_type=FindingType.INCOMPLETE_EXTRACTION,
                risk_level=RiskLevel.MEDIUM,
                explanation=(
                    "Incomplete extraction: required invoice information is missing. "
                    + "; ".join(reasons[:6])
                ),
            )
        ]
    if outcome.issues:
        return [
            FindingDraft(
                finding_type=FindingType.INVALID_EXTRACTION,
                risk_level=RiskLevel.MEDIUM,
                explanation=(
                    "Extraction needs review: " + "; ".join(outcome.issues[:6])
                ),
            )
        ]
    return []


def determine_status(
    outcome: ExtractionOutcome, findings: list[FindingDraft]
) -> InvoiceStatus:
    """High-confidence strong duplicates escalate; everything else is review."""
    if not outcome.success:
        return InvoiceStatus.FAILED
    if any(
        finding.risk_level == RiskLevel.HIGH
        and finding.finding_type
        in {FindingType.STRONG_INVOICE_DUPLICATE, FindingType.EXACT_FILE_DUPLICATE}
        for finding in findings
    ):
        return InvoiceStatus.FLAGGED_DUPLICATE
    if findings or not outcome.has_required_fields or outcome.issues:
        return InvoiceStatus.FLAGGED_REVIEW
    return InvoiceStatus.VERIFIED_LOGGED
