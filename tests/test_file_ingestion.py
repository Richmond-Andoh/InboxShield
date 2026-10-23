"""File validation, watcher, reconciliation, and processor resilience."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import FakeExtractor, valid_outcome
from watchdog.events import FileCreatedEvent, FileMovedEvent

from inboxshield.ingestion.file_utils import (
    FileNotStableError,
    FileValidationError,
    archive_copy,
    sha256_file,
    validate_source_file,
    wait_for_stable_file,
)
from inboxshield.ingestion.processor import InvoiceProcessor
from inboxshield.ingestion.reconciliation import discover_pending_files
from inboxshield.ingestion.watcher import InvoiceWatcher, _InvoiceEventHandler
from inboxshield.models import FindingType, InvoiceStatus, JobState


def test_validate_supported_file(settings, make_invoice_image) -> None:
    path = make_invoice_image()
    resolved = validate_source_file(path, settings)
    assert resolved == path.resolve()


def test_validate_rejects_unsupported_extension(settings) -> None:
    path = settings.incoming_dir / "notes.txt"
    path.write_text("not an invoice")
    with pytest.raises(FileValidationError, match="Unsupported"):
        validate_source_file(path, settings)


def test_validate_rejects_file_outside_incoming(settings, tmp_path) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"fake")
    with pytest.raises(FileValidationError, match="outside"):
        validate_source_file(outside, settings)


def test_validate_rejects_symlink(settings, tmp_path) -> None:
    target = tmp_path / "target.png"
    target.write_bytes(b"fake")
    link = settings.incoming_dir / "link.png"
    link.symlink_to(target)
    with pytest.raises(FileValidationError):
        validate_source_file(link, settings)


def test_validate_rejects_oversized_file(settings) -> None:
    small_limit = replace(settings, max_file_size_mb=1)
    path = settings.incoming_dir / "big.png"
    path.write_bytes(b"x" * (2 * 1024 * 1024))
    with pytest.raises(FileValidationError, match="size limit"):
        validate_source_file(path, small_limit)


def test_validate_rejects_empty_and_hidden(settings) -> None:
    empty = settings.incoming_dir / "empty.png"
    empty.write_bytes(b"")
    with pytest.raises(FileValidationError, match="empty"):
        validate_source_file(empty, settings)
    hidden = settings.incoming_dir / ".hidden.png"
    hidden.write_bytes(b"data")
    with pytest.raises(FileValidationError, match="hidden"):
        validate_source_file(hidden, settings)


def test_wait_for_stable_file(settings, make_invoice_image) -> None:
    path = make_invoice_image()
    wait_for_stable_file(path, settings)


def test_wait_for_stable_file_timeout(settings, make_invoice_image, monkeypatch) -> None:
    path = make_invoice_image()
    fast = replace(settings, stable_seconds=10.0, stable_timeout_seconds=0.3)
    with pytest.raises(FileNotStableError):
        wait_for_stable_file(path, fast)


def test_archive_copy_does_not_overwrite(settings, make_invoice_image) -> None:
    path = make_invoice_image()
    digest = sha256_file(path)
    destination = archive_copy(path, settings.processed_dir, digest)
    assert destination.exists()
    assert sha256_file(destination) == digest
    same = archive_copy(path, settings.processed_dir, digest)
    assert same == destination
    destination.write_bytes(b"tampered")
    with pytest.raises(FileValidationError, match="collision"):
        archive_copy(path, settings.processed_dir, digest)


def test_sha256_matches_hashlib(settings, make_invoice_image) -> None:
    path = make_invoice_image()
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert sha256_file(path) == expected


def test_watcher_enqueues_supported_created_file(settings, make_invoice_image) -> None:
    path = make_invoice_image()
    seen: list[Path] = []
    handler = _InvoiceEventHandler(settings, seen.append)
    handler.on_created(FileCreatedEvent(str(path)))
    assert seen == [path]


def test_watcher_ignores_unsupported_and_hidden(settings) -> None:
    seen: list[Path] = []
    handler = _InvoiceEventHandler(settings, seen.append)
    text = settings.incoming_dir / "note.txt"
    text.write_text("x")
    hidden = settings.incoming_dir / ".secret.png"
    hidden.write_bytes(b"x")
    handler.on_created(FileCreatedEvent(str(text)))
    handler.on_created(FileCreatedEvent(str(hidden)))
    assert seen == []


def test_watcher_handles_move(settings, make_invoice_image) -> None:
    path = make_invoice_image()
    seen: list[Path] = []
    handler = _InvoiceEventHandler(settings, seen.append)
    handler.on_moved(FileMovedEvent("/tmp/source.tmp", str(path)))
    assert seen == [path]


def test_watcher_starts_and_stops(settings) -> None:
    watcher = InvoiceWatcher(settings, lambda _path: None)
    watcher.start()
    try:
        assert watcher.running is True
    finally:
        watcher.stop()
    assert watcher.running is False


def test_reconciliation_finds_pending_and_skips_processed(
    settings, database, make_invoice_image
) -> None:
    pending = make_invoice_image(name="pending.png")
    processed = make_invoice_image(name="done.png", invoice_number="DONE-1")
    digest = sha256_file(processed)
    job_id = database.create_job(str(processed), digest)
    database.update_job(job_id, state=JobState.COMPLETED)
    found = discover_pending_files(settings, database)
    names = {path.name for path in found}
    assert pending.name in names
    assert processed.name not in names


def test_reconciliation_skips_invoice_hashes(
    settings, database, make_invoice_image
) -> None:
    path = make_invoice_image(name="already.png")
    digest = sha256_file(path)
    database.insert_invoice(
        {
            "filename": path.name,
            "file_path": str(path),
            "file_hash": digest,
            "vendor": "known vendor",
            "status": InvoiceStatus.VERIFIED_LOGGED.value,
        }
    )
    assert path not in discover_pending_files(settings, database)


def _processor(settings, database, extractor) -> InvoiceProcessor:
    return InvoiceProcessor(settings, database, extractor)


def test_processor_success_path(settings, database, make_invoice_image) -> None:
    path = make_invoice_image()
    extractor = FakeExtractor(valid_outcome())
    result = _processor(settings, database, extractor).process_file(path)
    assert result.state == JobState.COMPLETED
    assert result.status == InvoiceStatus.VERIFIED_LOGGED
    invoice = database.get_invoice(result.invoice_id)
    assert invoice is not None
    assert invoice["filename"] == path.name
    jobs = database.list_jobs()
    assert jobs[0]["state"] == "COMPLETED"
    assert jobs[0]["attempts"] == 1
    events = database.list_events(limit=50)
    event_types = {event["event_type"] for event in events}
    assert {"FILE_DETECTED", "EXTRACTION_STARTED", "EXTRACTION_COMPLETED", "INVOICE_SAVED"} <= event_types


def test_processor_is_idempotent_for_same_file(
    settings, database, make_invoice_image
) -> None:
    path = make_invoice_image()
    extractor = FakeExtractor(valid_outcome())
    processor = _processor(settings, database, extractor)
    first = processor.process_file(path)
    second = processor.process_file(path)
    assert first.state == JobState.COMPLETED
    assert second.state == JobState.SKIPPED_DUPLICATE
    assert second.invoice_id == first.invoice_id
    from inboxshield.database import InvoiceFilters

    _, total = database.search_invoices(InvoiceFilters())
    assert total == 1
    findings = database.list_findings(include_matched=True, duplicate_only=True)
    assert any(
        finding["finding_type"] == FindingType.EXACT_FILE_DUPLICATE.value
        for finding in findings
    )


def test_processor_rejects_unsupported_file(settings, database) -> None:
    path = settings.incoming_dir / "notes.txt"
    path.write_text("hello")
    result = _processor(settings, database, FakeExtractor()).process_file(path)
    assert result.state == JobState.FAILED
    assert "Unsupported" in result.message
    assert database.list_jobs() == []


def test_processor_handles_extractor_exception(
    settings, database, make_invoice_image
) -> None:
    path = make_invoice_image()
    extractor = FakeExtractor(error=RuntimeError("model exploded"))
    result = _processor(settings, database, extractor).process_file(path)
    assert result.state == JobState.FAILED
    assert result.retryable is False
    job = database.get_job_by_hash(sha256_file(path))
    assert job is not None
    assert job["state"] == "FAILED"
    assert "model exploded" in job["last_error"]


def test_processor_handles_extraction_failure_and_retry_limit(
    settings, database, make_invoice_image
) -> None:
    limited = replace(settings, max_retries=2)
    path = make_invoice_image()
    failure = valid_outcome(success=False, error="invalid model output", retryable=True)
    processor = _processor(limited, database, FakeExtractor(failure))
    first = processor.process_file(path)
    second = processor.process_file(path)
    third = processor.process_file(path)
    assert first.state == JobState.FAILED
    assert first.retryable is True
    assert second.state == JobState.FAILED
    assert second.retryable is False
    assert third.state == JobState.FAILED
    assert "Retry limit" in third.message
    assert database.count_jobs() == 1


def test_processor_incomplete_extraction_is_review(
    settings, database, make_invoice_image
) -> None:
    path = make_invoice_image()
    outcome = valid_outcome(amount=None, amount_minor=None, currency=None)
    result = _processor(settings, database, FakeExtractor(outcome)).process_file(path)
    assert result.state == JobState.COMPLETED
    assert result.status == InvoiceStatus.FLAGGED_REVIEW
    invoice = database.get_invoice(result.invoice_id)
    assert invoice is not None
    assert invoice["amount_minor"] is None
    findings = database.list_findings(invoice_id=result.invoice_id)
    assert any(finding["finding_type"] == "INCOMPLETE_EXTRACTION" for finding in findings)


def test_processor_blocks_concurrent_same_hash(
    settings, database, make_invoice_image
) -> None:
    path = make_invoice_image()
    processor = _processor(settings, database, FakeExtractor())
    digest = sha256_file(path)
    with processor._active_lock:
        processor._active_hashes.add(digest)
    result = processor.process_file(path)
    assert result.state == JobState.PROCESSING
    assert "already being processed" in result.message


def test_processor_does_not_delete_incoming_file(
    settings, database, make_invoice_image
) -> None:
    path = make_invoice_image()
    _processor(settings, database, FakeExtractor()).process_file(path)
    assert path.exists()
