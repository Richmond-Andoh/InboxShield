"""Safe CSV generation with spreadsheet-formula-injection protection."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from inboxshield.database import Database, InvoiceFilters

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_MAX_CELL_LENGTH = 4000


def sanitize_csv_cell(value: Any) -> str:
    """Neutralize spreadsheet formulas in untrusted text/fields."""
    if value is None:
        return ""
    text = str(value).replace("\x00", "")
    if len(text) > _MAX_CELL_LENGTH:
        text = text[:_MAX_CELL_LENGTH]
    stripped = text.lstrip(" \t\r\n\ufeff")
    if stripped.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _write_csv(headers: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([sanitize_csv_cell(row.get(header)) for header in headers])
    return buffer.getvalue()


INVOICE_COLUMNS = [
    "id",
    "vendor",
    "vendor_original",
    "invoice_number",
    "amount",
    "currency",
    "invoice_date",
    "due_date",
    "status",
    "review_status",
    "filename",
    "file_hash",
    "timestamp",
    "pages_inspected",
    "validation_notes",
    "error_message",
]

FINDING_COLUMNS = [
    "id",
    "invoice_id",
    "invoice_vendor",
    "finding_type",
    "risk_level",
    "explanation",
    "matched_invoice_id",
    "review_status",
    "reviewer_note",
    "created_at",
    "reviewed_at",
]


def invoices_to_csv(rows: Iterable[Mapping[str, Any]]) -> str:
    return _write_csv(INVOICE_COLUMNS, rows)


def findings_to_csv(rows: Iterable[Mapping[str, Any]]) -> str:
    return _write_csv(FINDING_COLUMNS, rows)


def export_invoices_csv(database: Database, filters: InvoiceFilters) -> tuple[str, int]:
    rows, total = database.search_invoices(filters)
    return invoices_to_csv(rows), total


def export_findings_csv(database: Database, *, pending_only: bool = False) -> str:
    rows = database.list_findings(
        include_matched=True, pending_only=pending_only, limit=5000
    )
    return findings_to_csv(rows)
