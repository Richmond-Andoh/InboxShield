"""Domain models and enumerations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class InvoiceStatus(StrEnum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    VERIFIED_LOGGED = "VERIFIED_LOGGED"
    FLAGGED_DUPLICATE = "FLAGGED_DUPLICATE"
    FLAGGED_REVIEW = "FLAGGED_REVIEW"
    FAILED = "FAILED"


class JobState(StrEnum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    FAILED = "FAILED"


class FindingType(StrEnum):
    EXACT_FILE_DUPLICATE = "EXACT_FILE_DUPLICATE"
    STRONG_INVOICE_DUPLICATE = "STRONG_INVOICE_DUPLICATE"
    POTENTIAL_DUPLICATE = "POTENTIAL_DUPLICATE"
    SIMILAR_VENDOR_MATCH = "SIMILAR_VENDOR_MATCH"
    INVOICE_NUMBER_CONFLICT = "INVOICE_NUMBER_CONFLICT"
    INCOMPLETE_EXTRACTION = "INCOMPLETE_EXTRACTION"
    INVALID_EXTRACTION = "INVALID_EXTRACTION"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    REVIEWED = "REVIEWED"


class EventType(StrEnum):
    FILE_DETECTED = "FILE_DETECTED"
    FILE_REJECTED = "FILE_REJECTED"
    EXTRACTION_STARTED = "EXTRACTION_STARTED"
    EXTRACTION_COMPLETED = "EXTRACTION_COMPLETED"
    DUPLICATE_CANDIDATE = "DUPLICATE_CANDIDATE"
    INVOICE_SAVED = "INVOICE_SAVED"
    PROCESSING_FAILED = "PROCESSING_FAILED"
    REVIEW_UPDATED = "REVIEW_UPDATED"
    DAEMON_STARTED = "DAEMON_STARTED"
    DAEMON_STOPPED = "DAEMON_STOPPED"
    HEALTH_WARNING = "HEALTH_WARNING"
    RECONCILED = "RECONCILED"


@dataclass(frozen=True)
class FindingDraft:
    finding_type: FindingType
    risk_level: RiskLevel
    explanation: str
    matched_invoice_id: int | None = None


@dataclass
class InvoiceCandidate:
    id: int | None
    vendor: str | None
    vendor_normalized: str | None
    invoice_number: str | None
    invoice_number_normalized: str | None
    amount_minor: int | None
    currency: str | None
    invoice_date: str | None
    filename: str = ""

    def label(self) -> str:
        return f"invoice #{self.id}" if self.id else f"document {self.filename}"


@dataclass
class ExtractionOutcome:
    """Result of the AI extraction stage (already normalized/validated)."""

    success: bool
    vendor: str | None = None
    vendor_original: str | None = None
    invoice_number: str | None = None
    amount: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    subtotal: str | None = None
    subtotal_minor: int | None = None
    tax_amount: str | None = None
    tax_minor: int | None = None
    pages_inspected: int = 0
    missing_fields: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    error: str | None = None
    retryable: bool = False
    raw_pages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_required_fields(self) -> bool:
        return bool(self.vendor and self.amount_minor is not None and self.currency)

    @property
    def needs_review(self) -> bool:
        return bool(self.issues) or not self.has_required_fields


@dataclass
class ProcessResult:
    state: JobState
    invoice_id: int | None = None
    status: InvoiceStatus | None = None
    message: str = ""
    retryable: bool = False
