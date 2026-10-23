"""Read-side queries used by the dashboard and export layer."""

from __future__ import annotations

from typing import Any

from inboxshield.config import Settings
from inboxshield.database import Database, InvoiceFilters


class InvoiceService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def metrics(self) -> dict[str, Any]:
        return self.database.overview_metrics()

    def search(
        self, filters: InvoiceFilters
    ) -> tuple[list[dict[str, Any]], int]:
        return self.database.search_invoices(filters)

    def get_detail(self, invoice_id: int) -> dict[str, Any] | None:
        invoice = self.database.get_invoice(invoice_id)
        if invoice is None:
            return None
        findings = self.database.list_findings(
            invoice_id=invoice_id, include_matched=True
        )
        related = self.database.list_findings(limit=200, duplicate_only=True)
        related = [
            finding
            for finding in related
            if finding["matched_invoice_id"] == invoice_id
            and finding["invoice_id"] != invoice_id
        ]
        return {
            "invoice": invoice,
            "findings": findings,
            "inbound_matches": related,
        }

    def pending_queue(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.database.pending_review_invoices(limit)

    def duplicate_pairs(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.database.list_findings(
            duplicate_only=True, include_matched=True, limit=limit
        )

    def findings(
        self, *, invoice_id: int | None = None, pending_only: bool = False
    ) -> list[dict[str, Any]]:
        return self.database.list_findings(
            invoice_id=invoice_id, include_matched=True, pending_only=pending_only
        )

    def verified_statuses(self) -> list[str]:
        return ["VERIFIED_LOGGED", "FLAGGED_DUPLICATE", "FLAGGED_REVIEW"]
