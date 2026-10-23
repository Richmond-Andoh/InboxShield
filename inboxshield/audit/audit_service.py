"""Audit orchestration: duplicate comparison plus transactional persistence."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from inboxshield.audit.duplicate_detector import detect_duplicates
from inboxshield.audit.risk_engine import determine_status, quality_findings
from inboxshield.config import Settings
from inboxshield.database import Database
from inboxshield.models import (
    EventType,
    ExtractionOutcome,
    FindingDraft,
    FindingType,
    InvoiceCandidate,
    InvoiceStatus,
    RiskLevel,
)
from inboxshield.utils.text import normalize_invoice_number, normalize_vendor

logger = logging.getLogger(__name__)


class AuditService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    @staticmethod
    def _candidate(outcome: ExtractionOutcome) -> InvoiceCandidate:
        return InvoiceCandidate(
            id=None,
            vendor=outcome.vendor,
            vendor_normalized=normalize_vendor(outcome.vendor),
            invoice_number=outcome.invoice_number,
            invoice_number_normalized=normalize_invoice_number(outcome.invoice_number),
            amount_minor=outcome.amount_minor,
            currency=outcome.currency,
            invoice_date=outcome.invoice_date,
        )

    def evaluate(self, outcome: ExtractionOutcome) -> list[FindingDraft]:
        findings = quality_findings(outcome)
        if outcome.success:
            existing = self.database.invoice_candidates()
            findings.extend(
                detect_duplicates(self._candidate(outcome), existing, self.settings)
            )
        return findings

    def store_invoice(
        self,
        outcome: ExtractionOutcome,
        *,
        filename: str,
        file_path: Path,
        file_hash: str,
        processing_started_at: str | None,
    ) -> tuple[int, InvoiceStatus, list[FindingDraft]]:
        """Insert invoice, findings, and events in one transaction."""
        notes = {
            "issues": outcome.issues,
            "missing_fields": outcome.missing_fields,
            "pages_inspected": outcome.pages_inspected,
        }
        with self.database.transaction() as connection:
            existing = self.database.invoice_candidates(connection=connection)
            findings = quality_findings(outcome)
            findings.extend(
                detect_duplicates(self._candidate(outcome), existing, self.settings)
            )
            status = determine_status(outcome, findings)
            invoice_id = self.database.insert_invoice(
                {
                    "filename": filename,
                    "file_path": str(file_path),
                    "file_hash": file_hash,
                    "vendor": outcome.vendor,
                    "vendor_original": outcome.vendor_original,
                    "invoice_number": outcome.invoice_number,
                    "amount": outcome.amount,
                    "amount_minor": outcome.amount_minor,
                    "currency": outcome.currency,
                    "invoice_date": outcome.invoice_date,
                    "due_date": outcome.due_date,
                    "subtotal": outcome.subtotal,
                    "subtotal_minor": outcome.subtotal_minor,
                    "tax_amount": outcome.tax_amount,
                    "tax_minor": outcome.tax_minor,
                    "status": status.value,
                    "review_status": "PENDING" if findings else "REVIEWED",
                    "validation_notes": json.dumps(notes),
                    "pages_inspected": outcome.pages_inspected,
                    "confidence": None,
                    "processing_started_at": processing_started_at,
                },
                connection=connection,
            )
            for finding in findings:
                self.database.add_finding(invoice_id, finding, connection=connection)
                self.database.add_event(
                    EventType.DUPLICATE_CANDIDATE,
                    finding.explanation,
                    invoice_id=invoice_id,
                    level="WARNING" if finding.risk_level != RiskLevel.LOW else "INFO",
                    connection=connection,
                )
            self.database.add_event(
                EventType.INVOICE_SAVED,
                f"Invoice #{invoice_id} saved with status {status.value}",
                invoice_id=invoice_id,
                connection=connection,
            )
        return invoice_id, status, findings

    def record_exact_file_duplicate(self, existing_invoice_id: int, filename: str) -> None:
        finding = FindingDraft(
            finding_type=FindingType.EXACT_FILE_DUPLICATE,
            risk_level=RiskLevel.HIGH,
            explanation=(
                f"Exact file duplicate: '{filename}' has the same SHA-256 hash as "
                f"invoice #{existing_invoice_id}. No second financial record was "
                "created. Confirm this was an intentional re-send before paying."
            ),
            matched_invoice_id=existing_invoice_id,
        )
        self.database.add_finding(existing_invoice_id, finding)
        self.database.add_event(
            EventType.DUPLICATE_CANDIDATE,
            finding.explanation,
            invoice_id=existing_invoice_id,
            level="WARNING",
        )
