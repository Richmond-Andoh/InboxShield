"""End-to-end pipeline tests with a test-only fake extraction client."""

from __future__ import annotations

from pathlib import Path

from conftest import valid_outcome

from inboxshield.audit.audit_service import AuditService
from inboxshield.config import Settings
from inboxshield.database import Database, InvoiceFilters
from inboxshield.ingestion.processor import InvoiceProcessor
from inboxshield.models import ExtractionOutcome, FindingType, InvoiceStatus, JobState


class SequenceExtractor:
    def __init__(self, outcomes: dict[str, ExtractionOutcome]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    def extract(self, path: Path) -> ExtractionOutcome:
        self.calls.append(path.name)
        return self.outcomes[path.name]


def _processor(settings: Settings, database: Database, extractor) -> InvoiceProcessor:
    return InvoiceProcessor(
        settings, database, extractor, AuditService(database, settings)
    )


def _outcome(
    *,
    vendor: str,
    vendor_original: str,
    number: str | None,
    amount_minor: int,
    currency: str = "GHS",
    date: str | None = "2026-10-20",
    issues: list[str] | None = None,
) -> ExtractionOutcome:
    return valid_outcome(
        vendor=vendor,
        vendor_original=vendor_original,
        invoice_number=number,
        amount_minor=amount_minor,
        amount=f"{amount_minor / 100:.2f}",
        currency=currency,
        invoice_date=date,
        issues=issues or [],
    )


def test_full_pipeline_flags_and_persists(settings, database, make_invoice_image) -> None:
    first = make_invoice_image(
        name="first.png", vendor="Accra Office Supplies Ltd", invoice_number="INV-1"
    )
    other = make_invoice_image(
        name="other.png", vendor="Kumasi Print Works", invoice_number="KP-1"
    )
    potential = make_invoice_image(
        name="potential.png", vendor="Accra Office Supplies Ltd", invoice_number="INV-2"
    )
    conflict = make_invoice_image(
        name="conflict.png",
        vendor="Accra Office Supplies Ltd",
        invoice_number="INV-1",
        total="1,420.00",
    )
    strong = make_invoice_image(
        name="strong.png",
        vendor="Accra Office Supplies Ltd",
        invoice_number="INV-1",
        total="1,250.01",
    )
    exact_copy = settings.incoming_dir / "exact_copy.png"
    exact_copy.write_bytes(first.read_bytes())

    outcomes = {
        first.name: _outcome(
            vendor="accra office supplies",
            vendor_original="Accra Office Supplies Ltd",
            number="INV-1",
            amount_minor=125000,
        ),
        other.name: _outcome(
            vendor="kumasi print works",
            vendor_original="Kumasi Print Works",
            number="KP-1",
            amount_minor=64000,
            date="2026-10-18",
        ),
        potential.name: _outcome(
            vendor="accra office supplies",
            vendor_original="Accra Office Supplies Ltd",
            number="INV-2",
            amount_minor=125000,
            date="2026-10-21",
        ),
        conflict.name: _outcome(
            vendor="accra office supplies",
            vendor_original="Accra Office Supplies Ltd",
            number="INV-1",
            amount_minor=142000,
            date="2026-10-22",
        ),
        strong.name: _outcome(
            vendor="accra office supplies",
            vendor_original="Accra Office Supplies Ltd",
            number="INV-1",
            amount_minor=125000,
        ),
    }
    processor = _processor(settings, database, SequenceExtractor(outcomes))

    first_result = processor.process_file(first)
    other_result = processor.process_file(other)
    potential_result = processor.process_file(potential)
    conflict_result = processor.process_file(conflict)
    strong_result = processor.process_file(strong)
    copy_result = processor.process_file(exact_copy)

    original_job = database.get_job_by_hash(
        __import__("hashlib").sha256(first.read_bytes()).hexdigest()
    )
    assert original_job is not None
    assert original_job["state"] == JobState.COMPLETED

    assert first_result.status == InvoiceStatus.VERIFIED_LOGGED
    assert other_result.status == InvoiceStatus.VERIFIED_LOGGED
    assert potential_result.status == InvoiceStatus.FLAGGED_REVIEW
    assert conflict_result.status == InvoiceStatus.FLAGGED_REVIEW
    assert strong_result.status == InvoiceStatus.FLAGGED_DUPLICATE
    assert copy_result.state == JobState.SKIPPED_DUPLICATE

    _, total = database.search_invoices(InvoiceFilters())
    assert total == 5

    potential_findings = database.list_findings(invoice_id=potential_result.invoice_id)
    assert any(
        finding["finding_type"] == FindingType.POTENTIAL_DUPLICATE.value
        for finding in potential_findings
    )
    conflict_findings = database.list_findings(invoice_id=conflict_result.invoice_id)
    assert any(
        finding["finding_type"] == FindingType.INVOICE_NUMBER_CONFLICT.value
        for finding in conflict_findings
    )
    strong_findings = database.list_findings(invoice_id=strong_result.invoice_id)
    assert any(
        finding["finding_type"] == FindingType.STRONG_INVOICE_DUPLICATE.value
        and finding["risk_level"] == "HIGH"
        for finding in strong_findings
    )
    exact_findings = database.list_findings(include_matched=True, duplicate_only=True)
    assert any(
        finding["finding_type"] == FindingType.EXACT_FILE_DUPLICATE.value
        for finding in exact_findings
    )

    metrics = database.overview_metrics()
    assert metrics["total_invoices"] == 5
    assert metrics["flagged_duplicate"] == 1
    assert metrics["flagged_review"] == 2
    assert metrics["verified"] == 2
    assert metrics["pending_findings"] >= 3
    gh = next(item for item in metrics["totals_by_currency"] if item["currency"] == "GHS")
    assert gh["total_minor"] == 125000 + 64000 + 125000 + 142000 + 125000


def test_different_currency_does_not_flag(settings, database, make_invoice_image) -> None:
    ghs = make_invoice_image(name="ghs.png", vendor="Vendor Ltd", invoice_number="V-1")
    usd = make_invoice_image(name="usd.png", vendor="Vendor Ltd", invoice_number="V-2")
    extractor = SequenceExtractor(
        {
            ghs.name: _outcome(
                vendor="vendor", vendor_original="Vendor Ltd", number="V-1",
                amount_minor=10000, currency="GHS",
            ),
            usd.name: _outcome(
                vendor="vendor", vendor_original="Vendor Ltd", number="V-2",
                amount_minor=10000, currency="USD", date="2026-10-21",
            ),
        }
    )
    processor = _processor(settings, database, extractor)
    processor.process_file(ghs)
    result = processor.process_file(usd)
    assert result.status == InvoiceStatus.VERIFIED_LOGGED
    assert database.list_findings(invoice_id=result.invoice_id) == []


def test_failed_file_does_not_block_next_file(
    settings, database, make_invoice_image
) -> None:
    bad = make_invoice_image(name="bad.png", vendor="Broken Vendor")
    good = make_invoice_image(name="good.png", vendor="Good Vendor")
    extractor = SequenceExtractor(
        {
            bad.name: valid_outcome(success=False, error="model output invalid"),
            good.name: _outcome(
                vendor="good vendor",
                vendor_original="Good Vendor",
                number="G-1",
                amount_minor=5000,
            ),
        }
    )
    processor = _processor(settings, database, extractor)
    first = processor.process_file(bad)
    second = processor.process_file(good)
    assert first.state == JobState.FAILED
    assert second.state == JobState.COMPLETED
    assert second.status == InvoiceStatus.VERIFIED_LOGGED


def test_review_action_persists_into_pipeline_data(
    settings, database, make_invoice_image
) -> None:
    first = make_invoice_image(name="a.png")
    second = make_invoice_image(name="b.png")
    extractor = SequenceExtractor(
        {
            first.name: _outcome(
                vendor="accra office supplies", vendor_original="Accra Office Supplies Ltd",
                number="INV-1", amount_minor=125000,
            ),
            second.name: _outcome(
                vendor="accra office supplies", vendor_original="Accra Office Supplies Ltd",
                number="INV-2", amount_minor=125000, date="2026-10-21",
            ),
        }
    )
    processor = _processor(settings, database, extractor)
    processor.process_file(first)
    result = processor.process_file(second)
    findings = database.list_findings(invoice_id=result.invoice_id)
    assert findings
    assert database.review_finding(int(findings[0]["id"]), "Confirmed with the vendor")
    reviewed = database.list_findings(invoice_id=result.invoice_id)[0]
    assert reviewed["review_status"] == "REVIEWED"
    invoice = database.get_invoice(result.invoice_id)
    assert invoice["review_status"] == "REVIEWED"
