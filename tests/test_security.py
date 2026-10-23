"""Security-focused tests: paths, SQL injection, secrets, and previews."""

from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import valid_outcome

from inboxshield.config import Settings
from inboxshield.dashboard.components import _allowed_preview_path, preview_bytes
from inboxshield.database import InvoiceFilters
from inboxshield.utils.logging_config import redact_secrets, sanitize_error


def test_sql_injection_is_parameterized(database) -> None:
    database.insert_invoice(
        {
            "filename": "a.png",
            "file_path": "/tmp/a.png",
            "file_hash": "1" * 64,
            "vendor": "legit vendor",
            "status": "VERIFIED_LOGGED",
        }
    )
    rows, total = database.search_invoices(
        InvoiceFilters(vendor="x' OR 1=1; DROP TABLE invoices; --")
    )
    assert total == 0
    assert rows == []
    _, still_there = database.search_invoices(InvoiceFilters())
    assert still_there == 1


def test_quoted_vendor_is_stored_literally(database) -> None:
    invoice_id = database.insert_invoice(
        {
            "filename": "quote.png",
            "file_path": "/tmp/quote.png",
            "file_hash": "2" * 64,
            "vendor": "O'Brien & Sons; DROP TABLE invoices",
            "status": "VERIFIED_LOGGED",
        }
    )
    invoice = database.get_invoice(invoice_id)
    assert invoice["vendor"] == "O'Brien & Sons; DROP TABLE invoices"
    assert database.integrity_check()


def test_preview_outside_allowed_directories_is_blocked(
    settings: Settings, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"private")
    assert _allowed_preview_path(str(outside), settings) is None
    assert preview_bytes(str(outside), settings) is None


def test_preview_traversal_is_blocked(settings: Settings) -> None:
    traversal = settings.incoming_dir / ".." / "secret.png"
    assert _allowed_preview_path(str(traversal), settings) is None


def test_preview_allowed_for_incoming_document(
    settings: Settings, make_invoice_image
) -> None:
    path = make_invoice_image()
    allowed = _allowed_preview_path(str(path), settings)
    assert allowed == path.resolve()
    assert preview_bytes(str(path), settings)


def test_preview_blocks_oversized_documents(settings: Settings, make_invoice_image) -> None:
    path = make_invoice_image()
    small = replace(settings, max_file_size_mb=0)
    assert _allowed_preview_path(str(path), small) is None


def test_secret_redaction() -> None:
    text = "password=hunter2 token=abc123 api_key=SECRET"
    redacted = redact_secrets(text)
    assert "hunter2" not in redacted
    assert "abc123" not in redacted
    assert "SECRET" not in redacted
    assert sanitize_error("Bearer abcdef123456").lower() == "bearer ***"


def test_error_sanitization_removes_tracebacks_and_truncates() -> None:
    message = "Traceback (most recent call last):\n  File \"x\", line 1\nValueError: bad"
    sanitized = sanitize_error(message, limit=80)
    assert "\n" not in sanitized
    assert len(sanitized) <= 80


def test_config_rejects_invalid_model_and_url() -> None:
    with pytest.raises(ValueError):
        replace(Settings(), model="not-a-model").validate()
    with pytest.raises(ValueError):
        replace(Settings(), ollama_url="file:///etc/passwd").validate()


def test_document_hash_is_sha256(settings: Settings, make_invoice_image) -> None:
    from inboxshield.ingestion.file_utils import sha256_file

    path = make_invoice_image()
    digest = sha256_file(path)
    assert len(digest) == 64
    assert digest == sha256_file(path)


def test_base64_payload_has_no_cloud_endpoint(settings: Settings) -> None:
    from inboxshield.ai.ollama_client import OllamaClient

    client = OllamaClient(settings)
    assert client.base_url == settings.ollama_url
    assert "localhost" in client.base_url or "127.0.0.1" in client.base_url
    assert base64.b64encode(b"test").decode()


def test_outcome_without_data_is_not_stored() -> None:
    from inboxshield.ai.validation import should_store_invoice

    assert should_store_invoice(valid_outcome(vendor=None, amount_minor=None,
        amount=None, currency=None, invoice_number=None, invoice_date=None)) is False
