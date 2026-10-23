"""Database initialization, integrity, transactions, and persistence."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from inboxshield.config import Settings
from inboxshield.database import Database, InvoiceFilters, utcnow
from inboxshield.models import (
    FindingDraft,
    FindingType,
    InvoiceStatus,
    JobState,
    RiskLevel,
)


def insert_invoice(
    database: Database,
    *,
    vendor: str = "acme supplies",
    filename: str = "invoice.png",
    file_hash: str = "a" * 64,
    amount_minor: int = 125000,
    currency: str = "GHS",
    status: str = InvoiceStatus.VERIFIED_LOGGED.value,
    invoice_number: str | None = "INV-1",
    invoice_date: str | None = "2026-10-20",
    timestamp: str | None = None,
) -> int:
    return database.insert_invoice(
        {
            "filename": filename,
            "file_path": f"/tmp/{filename}",
            "file_hash": file_hash,
            "vendor": vendor,
            "vendor_original": vendor.title(),
            "invoice_number": invoice_number,
            "amount": f"{amount_minor / 100:.2f}",
            "amount_minor": amount_minor,
            "currency": currency,
            "invoice_date": invoice_date,
            "status": status,
            "timestamp": timestamp or utcnow(),
        }
    )


def test_initialize_creates_schema(settings: Settings) -> None:
    database = Database(settings.db_path)
    database.initialize(settings)
    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "invoices",
            "audit_findings",
            "processing_jobs",
            "app_events",
            "settings",
            "daemon_heartbeat",
        } <= tables
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        assert foreign_keys == []


def test_initialize_is_idempotent(settings: Settings, database: Database) -> None:
    insert_invoice(database)
    database.initialize(settings)
    invoices, total = database.search_invoices(InvoiceFilters())
    assert total == 1
    assert len(invoices) == 1


def test_insert_and_read_invoice(database: Database) -> None:
    invoice_id = insert_invoice(database)
    invoice = database.get_invoice(invoice_id)
    assert invoice is not None
    assert invoice["vendor"] == "acme supplies"
    assert invoice["amount_minor"] == 125000
    assert invoice["currency"] == "GHS"
    assert invoice["status"] == InvoiceStatus.VERIFIED_LOGGED.value
    assert invoice["amount"] == "1250.00"


def test_missing_amount_stays_null(database: Database) -> None:
    invoice_id = database.insert_invoice(
        {
            "filename": "no-amount.png",
            "file_path": "/tmp/no-amount.png",
            "file_hash": "b" * 64,
            "vendor": "no amount vendor",
            "status": InvoiceStatus.FLAGGED_REVIEW.value,
        }
    )
    invoice = database.get_invoice(invoice_id)
    assert invoice is not None
    assert invoice["amount"] is None
    assert invoice["amount_minor"] is None


def test_transaction_rolls_back(database: Database) -> None:
    with pytest.raises(RuntimeError), database.transaction() as connection:
        database.insert_invoice(
            {
                "filename": "rollback.png",
                "file_path": "/tmp/rollback.png",
                "file_hash": "c" * 64,
                "status": InvoiceStatus.VERIFIED_LOGGED.value,
            },
            connection=connection,
        )
        raise RuntimeError("boom")
    _, total = database.search_invoices(InvoiceFilters())
    assert total == 0


def test_foreign_key_integrity(database: Database) -> None:
    finding = FindingDraft(
        finding_type=FindingType.POTENTIAL_DUPLICATE,
        risk_level=RiskLevel.MEDIUM,
        explanation="test",
    )
    with pytest.raises(sqlite3.IntegrityError):
        database.add_finding(99999, finding)


def test_status_constraint(database: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        insert_invoice(database, status="NOT_A_STATUS")


def test_persistence_across_new_connection(
    settings: Settings, database: Database
) -> None:
    invoice_id = insert_invoice(database)
    reopened = Database(settings.db_path)
    invoice = reopened.get_invoice(invoice_id)
    assert invoice is not None
    assert invoice["id"] == invoice_id
    assert reopened.integrity_check() is True


def test_audit_findings_are_unique_per_match(database: Database) -> None:
    invoice_id = insert_invoice(database)
    other_id = insert_invoice(
        database, file_hash="d" * 64, filename="other.png", invoice_number="INV-2"
    )
    finding = FindingDraft(
        finding_type=FindingType.POTENTIAL_DUPLICATE,
        risk_level=RiskLevel.MEDIUM,
        explanation="Possible duplicate",
        matched_invoice_id=other_id,
    )
    database.add_finding(invoice_id, finding)
    database.add_finding(invoice_id, finding)
    findings = database.list_findings(invoice_id=invoice_id)
    assert len(findings) == 1
    assert findings[0]["matched_invoice_id"] == other_id


def test_review_updates_invoice_and_audit_history(database: Database) -> None:
    invoice_id = insert_invoice(database, status=InvoiceStatus.FLAGGED_REVIEW.value)
    finding = FindingDraft(
        finding_type=FindingType.INCOMPLETE_EXTRACTION,
        risk_level=RiskLevel.MEDIUM,
        explanation="Missing amount",
    )
    database.add_finding(invoice_id, finding)
    stored = database.list_findings(invoice_id=invoice_id)[0]
    assert database.review_finding(int(stored["id"]), "Checked with vendor")
    refreshed = database.get_invoice(invoice_id)
    assert refreshed is not None
    assert refreshed["review_status"] == "REVIEWED"
    reviewed = database.list_findings(invoice_id=invoice_id)[0]
    assert reviewed["review_status"] == "REVIEWED"
    assert reviewed["reviewer_note"] == "Checked with vendor"
    events = database.list_events(limit=10, invoice_id=invoice_id)
    assert any(event["event_type"] == "REVIEW_UPDATED" for event in events)


def test_search_filters_and_pagination(database: Database) -> None:
    insert_invoice(database, vendor="alpha ltd", file_hash="1" * 64, amount_minor=10000)
    insert_invoice(
        database,
        vendor="beta plc",
        file_hash="2" * 64,
        amount_minor=50000,
        currency="USD",
        status=InvoiceStatus.FLAGGED_REVIEW.value,
    )
    insert_invoice(
        database,
        vendor="gamma co",
        file_hash="3" * 64,
        amount_minor=90000,
        status=InvoiceStatus.FLAGGED_DUPLICATE.value,
    )
    rows, total = database.search_invoices(InvoiceFilters(vendor="beta"))
    assert total == 1
    assert rows[0]["vendor"] == "beta plc"

    rows, total = database.search_invoices(
        InvoiceFilters(statuses=(InvoiceStatus.FLAGGED_REVIEW.value,))
    )
    assert total == 1

    rows, total = database.search_invoices(InvoiceFilters(currency="USD"))
    assert total == 1

    rows, total = database.search_invoices(
        InvoiceFilters(min_amount_minor=40000, max_amount_minor=100000)
    )
    assert total == 2

    first_page, total = database.search_invoices(
        InvoiceFilters(limit=2, offset=0, sort="amount", descending=False)
    )
    second_page, _ = database.search_invoices(
        InvoiceFilters(limit=2, offset=2, sort="amount", descending=False)
    )
    assert total == 3
    assert len(first_page) == 2
    assert len(second_page) == 1
    assert first_page[0]["amount_minor"] < first_page[1]["amount_minor"]


def test_metrics_are_derived_from_records(database: Database) -> None:
    insert_invoice(database, file_hash="4" * 64, amount_minor=100000)
    insert_invoice(
        database,
        file_hash="5" * 64,
        amount_minor=50000,
        currency="USD",
        status=InvoiceStatus.FLAGGED_REVIEW.value,
    )
    insert_invoice(
        database,
        file_hash="6" * 64,
        amount_minor=25000,
        status=InvoiceStatus.FLAGGED_DUPLICATE.value,
    )
    metrics = database.overview_metrics()
    assert metrics["total_invoices"] == 3
    assert metrics["verified"] == 1
    assert metrics["flagged_review"] == 1
    assert metrics["flagged_duplicate"] == 1
    totals = {item["currency"]: item["total_minor"] for item in metrics["totals_by_currency"]}
    assert totals == {"GHS": 125000, "USD": 50000}
    assert len(metrics["totals_by_currency"]) == 2


def test_settings_and_heartbeat(database: Database, settings: Settings) -> None:
    assert database.get_setting("model") == settings.model
    database.set_setting("dashboard_refresh_seconds", "9")
    assert database.get_setting("dashboard_refresh_seconds") == "9"
    database.start_heartbeat(123, settings.model, "1.0.0")
    heartbeat = database.get_heartbeat()
    assert heartbeat is not None
    assert heartbeat["pid"] == 123
    assert heartbeat["status"] == "RUNNING"
    database.stop_heartbeat()
    assert database.get_heartbeat()["status"] == "STOPPED"


def test_event_filter_by_invoice(database: Database) -> None:
    invoice_id = insert_invoice(database)
    database.add_event("FILE_DETECTED", "seen", invoice_id=invoice_id)
    database.add_event("FILE_DETECTED", "other")
    events = database.list_events(invoice_id=invoice_id)
    assert len(events) == 1
    assert events[0]["message"] == "seen"


def test_reset_interrupted_jobs(database: Database) -> None:
    job_id = database.create_job("/tmp/a.png", "f" * 64)
    database.update_job(job_id, state=JobState.PROCESSING)
    assert database.reset_interrupted_jobs() == 1
    job = database.get_job_by_hash("f" * 64)
    assert job is not None
    assert job["state"] == "FAILED"


def test_database_file_created_in_expected_directory(
    settings: Settings, database: Database
) -> None:
    assert Path(settings.db_path).exists()
    assert database.db_size_bytes() > 0
