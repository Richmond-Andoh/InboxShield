"""CSV export correctness and formula-injection protection."""

from __future__ import annotations

import csv
import io

from inboxshield.database import InvoiceFilters
from inboxshield.models import FindingDraft, FindingType, RiskLevel
from inboxshield.services.export_service import (
    export_findings_csv,
    export_invoices_csv,
    findings_to_csv,
    invoices_to_csv,
    sanitize_csv_cell,
)


def test_formula_cells_are_neutralized() -> None:
    assert sanitize_csv_cell("=1+1") == "'=1+1"
    assert sanitize_csv_cell("+1") == "'+1"
    assert sanitize_csv_cell("-1") == "'-1"
    assert sanitize_csv_cell("@cmd") == "'@cmd"
    assert sanitize_csv_cell("  =SUM(A1)") == "'  =SUM(A1)"
    assert sanitize_csv_cell("Acme Supplies") == "Acme Supplies"
    assert sanitize_csv_cell(None) == ""


def test_invoice_csv_is_quoted_and_safe() -> None:
    csv_text = invoices_to_csv(
        [
            {
                "id": 1,
                "vendor": "=HYPERLINK(\"http://evil\")",
                "filename": "normal.png",
                "amount": "10.00",
            }
        ]
    )
    assert "'=HYPERLINK" in csv_text
    reader = csv.reader(io.StringIO(csv_text))
    header = next(reader)
    row = next(reader)
    assert header[0] == "id"
    assert row[0] == "1"


def test_filtered_invoice_export(database) -> None:
    database.insert_invoice(
        {
            "filename": "a.png",
            "file_path": "/tmp/a.png",
            "file_hash": "a" * 64,
            "vendor": "alpha",
            "amount": "10.00",
            "amount_minor": 1000,
            "currency": "GHS",
            "status": "VERIFIED_LOGGED",
        }
    )
    database.insert_invoice(
        {
            "filename": "b.png",
            "file_path": "/tmp/b.png",
            "file_hash": "b" * 64,
            "vendor": "beta",
            "amount": "20.00",
            "amount_minor": 2000,
            "currency": "GHS",
            "status": "VERIFIED_LOGGED",
        }
    )
    csv_text, count = export_invoices_csv(database, InvoiceFilters(vendor="alpha"))
    assert count == 1
    assert "alpha" in csv_text
    assert "beta" not in csv_text


def test_findings_export_includes_review_fields(database) -> None:
    invoice_id = database.insert_invoice(
        {
            "filename": "a.png",
            "file_path": "/tmp/a.png",
            "file_hash": "c" * 64,
            "vendor": "alpha",
            "status": "FLAGGED_REVIEW",
        }
    )
    database.add_finding(
        invoice_id,
        FindingDraft(
            finding_type=FindingType.INCOMPLETE_EXTRACTION,
            risk_level=RiskLevel.MEDIUM,
            explanation="Missing amount",
        ),
    )
    csv_text = export_findings_csv(database)
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["review_status"] == "PENDING"
    assert rows[0]["finding_type"] == "INCOMPLETE_EXTRACTION"


def test_empty_database_export_has_header_only(database) -> None:
    csv_text, count = export_invoices_csv(database, InvoiceFilters())
    assert count == 0
    lines = csv_text.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith('"id"')


def test_findings_to_csv_handles_empty_list() -> None:
    text = findings_to_csv([])
    assert text.strip().startswith('"id"')
