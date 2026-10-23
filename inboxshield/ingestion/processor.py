"""Single-document processing pipeline.

The processor is deliberately independent of Watchdog and the dashboard so it
can be exercised directly in tests and used by the ``--once`` daemon mode.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from inboxshield.ai.extractor import DocumentExtractor
from inboxshield.ai.validation import should_store_invoice
from inboxshield.audit.audit_service import AuditService
from inboxshield.config import Settings
from inboxshield.database import Database, utcnow
from inboxshield.ingestion.file_utils import (
    FileNotStableError,
    FileValidationError,
    archive_copy,
    sha256_file,
    validate_source_file,
    wait_for_stable_file,
)
from inboxshield.models import EventType, JobState, ProcessResult
from inboxshield.utils.logging_config import sanitize_error

logger = logging.getLogger(__name__)


class InvoiceProcessor:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        extractor: DocumentExtractor,
        audit_service: AuditService | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.extractor = extractor
        self.audit_service = audit_service or AuditService(database, settings)
        self._active_hashes: set[str] = set()
        self._active_lock = threading.Lock()

    # ------------------------------------------------------------------ public
    def process_file(self, path: Path) -> ProcessResult:
        name = path.name
        try:
            resolved = validate_source_file(path, self.settings)
        except FileValidationError as exc:
            self.database.add_event(
                EventType.FILE_REJECTED, f"Rejected {name}: {exc}", level="WARNING"
            )
            logger.warning("Rejected file %s: %s", name, exc)
            return ProcessResult(state=JobState.FAILED, message=str(exc), retryable=False)

        try:
            wait_for_stable_file(resolved, self.settings)
        except FileNotStableError as exc:
            self.database.add_event(
                EventType.FILE_REJECTED,
                f"File not ready {name}: {exc}",
                level="WARNING",
            )
            logger.warning("File not ready %s: %s", name, exc)
            return ProcessResult(state=JobState.QUEUED, message=str(exc), retryable=True)

        try:
            file_hash = sha256_file(resolved)
        except OSError as exc:
            message = f"Could not hash {name}: {sanitize_error(exc, 200)}"
            self.database.add_event(EventType.FILE_REJECTED, message, level="ERROR")
            return ProcessResult(state=JobState.FAILED, message=message, retryable=True)

        with self._active_lock:
            if file_hash in self._active_hashes:
                return ProcessResult(
                    state=JobState.PROCESSING,
                    message=f"{name} is already being processed",
                    retryable=False,
                )
            self._active_hashes.add(file_hash)

        try:
            return self._process_locked(resolved, file_hash)
        finally:
            with self._active_lock:
                self._active_hashes.discard(file_hash)

    # ----------------------------------------------------------------- private
    def _process_locked(self, path: Path, file_hash: str) -> ProcessResult:
        existing_invoice = self.database.get_invoice_by_hash(file_hash)
        job = self.database.get_job_by_hash(file_hash)
        if existing_invoice is not None:
            return self._handle_exact_duplicate(path, file_hash, existing_invoice, job)

        if job is not None and job["state"] in (
            JobState.COMPLETED.value,
            JobState.SKIPPED_DUPLICATE.value,
        ):
            return ProcessResult(
                state=JobState(job["state"]),
                invoice_id=job.get("invoice_id"),
                message="File was already processed",
            )
        if job is not None and job["state"] == JobState.PROCESSING.value:
            return ProcessResult(
                state=JobState.PROCESSING,
                message="A processing job for this file is already running",
            )
        if (
            job is not None
            and job["state"] == JobState.FAILED.value
            and int(job["attempts"]) >= self.settings.max_retries
        ):
            return ProcessResult(
                state=JobState.FAILED,
                message=(
                    f"Retry limit reached ({self.settings.max_retries}); "
                    f"last error: {job.get('last_error') or 'unknown'}"
                ),
                retryable=False,
            )

        job_id = (
            int(job["id"])
            if job is not None
            else self.database.create_job(str(path), file_hash)
        )
        if job_id == 0:
            job = self.database.get_job_by_hash(file_hash)
            if job is None:
                return ProcessResult(
                    state=JobState.FAILED,
                    message="Could not register processing job",
                    retryable=True,
                )
            job_id = int(job["id"])
        self.database.update_job(
            job_id,
            state=JobState.PROCESSING,
            increment_attempts=True,
            started=True,
        )
        self.database.add_event(
            EventType.FILE_DETECTED, f"Detected {path.name} ({file_hash[:12]})"
        )

        archive_path = path
        if self.settings.archive_processed_files:
            try:
                archive_path = archive_copy(
                    path, self.settings.processed_dir, file_hash
                )
            except (OSError, FileValidationError) as exc:
                logger.warning(
                    "Could not archive %s; using the incoming copy: %s",
                    path.name,
                    sanitize_error(exc, 150),
                )
                self.database.add_event(
                    EventType.HEALTH_WARNING,
                    f"Archive copy failed for {path.name}: {sanitize_error(exc, 150)}",
                    level="WARNING",
                )

        self.database.add_event(
            EventType.EXTRACTION_STARTED,
            f"Local extraction started for {path.name} using {self.settings.model}",
        )
        processing_started_at = utcnow()
        try:
            outcome = self.extractor.extract(archive_path)
        except Exception as exc:
            message = f"Extraction crashed for {path.name}: {sanitize_error(exc, 300)}"
            logger.exception("Extraction crashed for %s", path.name)
            self.database.update_job(
                job_id, state=JobState.FAILED, error=message, completed=True
            )
            self.database.add_event(
                EventType.PROCESSING_FAILED, message, level="ERROR"
            )
            return ProcessResult(
                state=JobState.FAILED, message=message, retryable=False
            )

        if not outcome.success or not should_store_invoice(outcome):
            message = outcome.error or "Extraction returned no usable invoice data"
            retryable = outcome.retryable and int((job or {}).get("attempts", 0)) + 1 < self.settings.max_retries
            self.database.update_job(
                job_id,
                state=JobState.FAILED,
                error=sanitize_error(message, 400),
                completed=True,
            )
            self.database.add_event(
                EventType.PROCESSING_FAILED,
                f"Processing failed for {path.name}: {sanitize_error(message, 300)}",
                level="ERROR",
            )
            logger.error("Processing failed for %s: %s", path.name, message)
            return ProcessResult(
                state=JobState.FAILED, message=message, retryable=retryable
            )

        try:
            invoice_id, status, findings = self.audit_service.store_invoice(
                outcome,
                filename=path.name,
                file_path=archive_path,
                file_hash=file_hash,
                processing_started_at=processing_started_at,
            )
        except Exception as exc:
            message = f"Could not save invoice for {path.name}: {sanitize_error(exc, 300)}"
            logger.exception("Database write failed for %s", path.name)
            self.database.update_job(
                job_id, state=JobState.FAILED, error=message, completed=True
            )
            self.database.add_event(
                EventType.PROCESSING_FAILED, message, level="ERROR"
            )
            return ProcessResult(
                state=JobState.FAILED, message=message, retryable=True
            )

        self.database.update_job(
            job_id,
            state=JobState.COMPLETED,
            invoice_id=invoice_id,
            completed=True,
        )
        self.database.add_event(
            EventType.EXTRACTION_COMPLETED,
            f"Extraction completed for {path.name}: {status.value} "
            f"({len(findings)} finding(s), {outcome.pages_inspected} page(s))",
            invoice_id=invoice_id,
        )
        logger.info(
            "Processed %s -> invoice #%s (%s), %d finding(s)",
            path.name,
            invoice_id,
            status.value,
            len(findings),
        )
        return ProcessResult(
            state=JobState.COMPLETED,
            invoice_id=invoice_id,
            status=status,
            message=f"Invoice #{invoice_id} stored as {status.value}",
        )

    def _handle_exact_duplicate(
        self,
        path: Path,
        file_hash: str,
        invoice: dict,
        job: dict | None,
    ) -> ProcessResult:
        invoice_id = int(invoice["id"])
        if job is None:
            job_id = self.database.create_job(str(path), file_hash)
            self.database.update_job(
                job_id,
                state=JobState.SKIPPED_DUPLICATE,
                invoice_id=invoice_id,
                completed=True,
                increment_attempts=True,
                started=True,
            )
        self.audit_service.record_exact_file_duplicate(invoice_id, path.name)
        logger.info(
            "Exact file duplicate %s matches invoice #%s; no new record created",
            path.name,
            invoice_id,
        )
        return ProcessResult(
            state=JobState.SKIPPED_DUPLICATE,
            invoice_id=invoice_id,
            message=(
                f"Exact duplicate of invoice #{invoice_id}; "
                "no second financial record was created"
            ),
        )
