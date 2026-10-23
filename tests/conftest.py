"""Shared test fixtures. No network, model, or production database required."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from inboxshield.config import Settings
from inboxshield.database import Database
from inboxshield.models import ExtractionOutcome


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    incoming = tmp_path / "incoming_invoices"
    processed = tmp_path / "processed_invoices"
    data = tmp_path / "data"
    logs = tmp_path / "logs"
    config = Settings(
        home=tmp_path,
        incoming_dir=incoming,
        processed_dir=processed,
        data_dir=data,
        logs_dir=logs,
        db_path=data / "test.db",
        log_file=logs / "test.log",
        stable_seconds=0.05,
        stable_timeout_seconds=3.0,
        retry_backoff_seconds=0.0,
        max_retries=2,
        archive_processed_files=False,
        dashboard_page_size=10,
        environment="test",
    )
    config.ensure_directories()
    return config


@pytest.fixture
def database(settings: Settings) -> Database:
    db = Database(settings.db_path)
    db.initialize(settings)
    return db


@pytest.fixture
def make_invoice_image(settings: Settings):
    counter = {"value": 0}

    def _make(
        *,
        vendor: str = "Accra Office Supplies Ltd",
        invoice_number: str = "INV-2026-0042",
        total: str = "1,250.00",
        currency: str = "GHS",
        name: str | None = None,
    ) -> Path:
        counter["value"] += 1
        filename = name or f"invoice_{counter['value']}.png"
        path = settings.incoming_dir / filename
        image = Image.new("RGB", (900, 1200), "white")
        draw = ImageDraw.Draw(image)
        draw.text((50, 50), vendor, fill="black")
        draw.text((50, 100), f"Invoice Number: {invoice_number}", fill="black")
        draw.text((50, 150), "Invoice Date: 2026-10-20", fill="black")
        draw.text((50, 220), "Description", fill="black")
        draw.text((50, 270), "Office supplies", fill="black")
        draw.text((50, 900), f"TOTAL DUE: {currency} {total}", fill="black")
        image.save(path)
        return path

    return _make


def valid_outcome(**overrides: Any) -> ExtractionOutcome:
    values: dict[str, Any] = {
        "success": True,
        "vendor": "accra office supplies",
        "vendor_original": "Accra Office Supplies Ltd",
        "invoice_number": "INV-2026-0042",
        "amount": "1250.00",
        "amount_minor": 125000,
        "currency": "GHS",
        "invoice_date": "2026-10-20",
        "due_date": "2026-11-20",
        "subtotal": "1100.00",
        "subtotal_minor": 110000,
        "tax_amount": "150.00",
        "tax_minor": 15000,
        "pages_inspected": 1,
        "missing_fields": [],
        "issues": [],
    }
    values.update(overrides)
    return ExtractionOutcome(**values)


class FakeExtractor:
    """Test-only extractor. Never used by the production daemon."""

    def __init__(self, outcome: ExtractionOutcome | None = None, error: Exception | None = None):
        self.outcome = outcome or valid_outcome()
        self.error = error
        self.calls: list[Path] = []

    def extract(self, path: Path) -> ExtractionOutcome:
        self.calls.append(path)
        if self.error is not None:
            raise self.error
        return self.outcome


@pytest.fixture
def fake_extractor() -> FakeExtractor:
    return FakeExtractor()


@pytest.fixture
def valid_extraction() -> ExtractionOutcome:
    return valid_outcome()
