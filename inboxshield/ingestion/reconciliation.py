"""Startup and periodic reconciliation for missed filesystem events."""

from __future__ import annotations

import logging
from pathlib import Path

from inboxshield.config import Settings
from inboxshield.database import Database
from inboxshield.ingestion.file_utils import (
    is_hidden_or_temporary,
    is_supported,
    sha256_file,
)

logger = logging.getLogger(__name__)


def discover_pending_files(
    settings: Settings, database: Database
) -> list[Path]:
    """Return supported incoming files that have no job or invoice yet.

    Hashing each candidate is intentional: it also catches files restored or
    renamed between daemon runs.  The watched directory is expected to be small;
    files already recorded are skipped without expensive extraction.
    """
    pending: list[Path] = []
    if not settings.incoming_dir.is_dir():
        logger.warning("Watched directory does not exist: %s", settings.incoming_dir)
        return pending
    for path in sorted(settings.incoming_dir.iterdir()):
        if not path.is_file() or path.is_symlink():
            continue
        if is_hidden_or_temporary(path) or not is_supported(path, settings):
            continue
        try:
            file_hash = sha256_file(path)
        except OSError as exc:
            logger.warning("Cannot hash %s during reconciliation: %s", path.name, exc)
            continue
        if database.get_invoice_by_hash(file_hash) is not None:
            continue
        job = database.get_job_by_hash(file_hash)
        if job is not None and job["state"] in ("COMPLETED", "SKIPPED_DUPLICATE"):
            continue
        pending.append(path)
    return pending
