"""Dashboard data, health, and review-state behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from inboxshield.database import Database, InvoiceFilters
from inboxshield.models import FindingDraft, FindingType, InvoiceStatus, RiskLevel
from inboxshield.services.health_service import HealthService, daemon_status
from inboxshield.services.invoice_service import InvoiceService


def _insert(database: Database, **overrides):
    values = {
        "filename": "invoice.png",
        "file_path": "/tmp/invoice.png",
        "file_hash": "a" * 64,
        "vendor": "acme",
        "vendor_original": "Acme Ltd",
        "invoice_number": "INV-1",
        "amount": "100.00",
        "amount_minor": 10000,
        "currency": "GHS",
        "invoice_date": "2026-10-20",
        "status": InvoiceStatus.VERIFIED_LOGGED.value,
        "review_status": "REVIEWED",
    }
    values.update(overrides)
    return database.insert_invoice(values)


def test_service_metrics_match_database(database, settings) -> None:
    _insert(database)
    _insert(
        database,
        file_hash="b" * 64,
        status=InvoiceStatus.FLAGGED_REVIEW.value,
        review_status="PENDING",
        currency="USD",
        amount_minor=5000,
        amount="50.00",
    )
    service = InvoiceService(database, settings)
    metrics = service.metrics()
    assert metrics["total_invoices"] == 2
    assert metrics["verified"] == 1
    assert metrics["flagged_review"] == 1
    assert metrics["awaiting_review"] == 1
    assert {item["currency"] for item in metrics["totals_by_currency"]} == {"GHS", "USD"}


def test_empty_database_metrics_are_zero(database, settings) -> None:
    metrics = InvoiceService(database, settings).metrics()
    assert metrics["total_invoices"] == 0
    assert metrics["total_documents"] == 0
    assert metrics["totals_by_currency"] == []
    assert metrics["awaiting_review"] == 0


def test_detail_includes_findings_and_matched_invoice(database, settings) -> None:
    primary = _insert(database)
    matched = _insert(
        database, file_hash="c" * 64, filename="matched.png", invoice_number="INV-2"
    )
    database.add_finding(
        primary,
        FindingDraft(
            finding_type=FindingType.POTENTIAL_DUPLICATE,
            risk_level=RiskLevel.MEDIUM,
            explanation="Possible duplicate",
            matched_invoice_id=matched,
        ),
    )
    detail = InvoiceService(database, settings).get_detail(primary)
    assert detail is not None
    assert detail["invoice"]["id"] == primary
    assert len(detail["findings"]) == 1
    inbound = InvoiceService(database, settings).get_detail(matched)
    assert inbound is not None
    assert len(inbound["inbound_matches"]) == 1


def test_review_state_updates_and_queue_clears(database, settings) -> None:
    invoice_id = _insert(
        database,
        status=InvoiceStatus.FLAGGED_REVIEW.value,
        review_status="PENDING",
    )
    database.add_finding(
        invoice_id,
        FindingDraft(
            finding_type=FindingType.INCOMPLETE_EXTRACTION,
            risk_level=RiskLevel.MEDIUM,
            explanation="Missing amount",
        ),
    )
    service = InvoiceService(database, settings)
    assert len(service.pending_queue()) == 1
    finding = database.list_findings(invoice_id=invoice_id)[0]
    assert database.review_finding(int(finding["id"]), "Verified with vendor")
    assert service.pending_queue() == []
    assert service.metrics()["awaiting_review"] == 0
    events = database.list_events(limit=10, invoice_id=invoice_id)
    assert any(event["event_type"] == "REVIEW_UPDATED" for event in events)


def test_search_filtering_via_service(database, settings) -> None:
    _insert(database, vendor="alpha")
    _insert(database, file_hash="d" * 64, vendor="beta", currency="USD")
    rows, total = InvoiceService(database, settings).search(
        InvoiceFilters(vendor="beta")
    )
    assert total == 1
    assert rows[0]["vendor"] == "beta"


def test_health_service_reports_failure_without_raising(database, settings) -> None:
    class StubClient:
        def model_status(self):
            return {
                "available": False,
                "models": [],
                "model_present": False,
                "capabilities": [],
                "error": "connection refused",
            }

    health = HealthService(database, settings, client=StubClient()).check()
    assert health["ollama"]["available"] is False
    assert health["database"]["ok"] is True
    assert health["directories"]["incoming"]["writable"] is True
    assert health["daemon"]["running"] is False


def test_daemon_heartbeat_fresh_and_stale(database, settings) -> None:
    database.start_heartbeat(999, settings.model, "1.0.0")
    assert daemon_status(database, settings)["running"] is True

    old = (datetime.now(UTC) - timedelta(minutes=10)).replace(microsecond=0).isoformat()
    with database.connect() as connection:
        connection.execute(
            "UPDATE daemon_heartbeat SET heartbeat_at = ? WHERE id = 1", (old,)
        )
        connection.commit()
    status = daemon_status(database, settings)
    assert status["running"] is False
    assert status["seconds_since_heartbeat"] > 60


def test_overview_metrics_does_not_merge_currencies(database) -> None:
    _insert(database)
    _insert(database, file_hash="e" * 64, currency="USD", amount_minor=10000)
    totals = database.overview_metrics()["totals_by_currency"]
    assert len(totals) == 2
    assert all(item["total_minor"] == 10000 for item in totals)
