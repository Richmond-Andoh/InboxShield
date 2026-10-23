"""Safe filesystem helpers for document ingestion."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
from pathlib import Path

from inboxshield.config import Settings

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
TEMP_PREFIXES = ("~$", ".~", ".tmp", "~")


class FileValidationError(RuntimeError):
    pass


class FileNotStableError(RuntimeError):
    pass


def is_within(path: Path, directory: Path) -> bool:
    try:
        resolved = path.resolve(strict=False)
        root = directory.resolve(strict=False)
    except OSError:
        return False
    return resolved == root or root in resolved.parents


def is_supported(path: Path, settings: Settings) -> bool:
    return path.suffix.lower() in settings.supported_extensions


def is_hidden_or_temporary(path: Path) -> bool:
    name = path.name
    return name.startswith(".") or name.startswith(TEMP_PREFIXES)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_source_file(path: Path, settings: Settings) -> Path:
    """Validate containment, readability, type and size; return resolved path."""
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FileValidationError(f"File does not exist or is unreadable: {path.name}") from exc
    if not resolved.is_file():
        raise FileValidationError(f"Not a regular file: {path.name}")
    if path.is_symlink():
        raise FileValidationError(f"Symbolic links are not accepted: {path.name}")
    if not is_within(resolved, settings.incoming_dir):
        raise FileValidationError(
            f"File is outside the watched directory: {path.name}"
        )
    if not is_supported(resolved, settings):
        raise FileValidationError(
            f"Unsupported file type '{resolved.suffix or '(none)'}'. "
            f"Allowed: {', '.join(settings.supported_extensions)}"
        )
    if is_hidden_or_temporary(resolved):
        raise FileValidationError(f"Ignoring temporary or hidden file: {path.name}")
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        raise FileValidationError(f"Could not stat file: {path.name}") from exc
    if size == 0:
        raise FileValidationError(f"File is empty: {path.name}")
    if size > settings.max_file_size_bytes:
        raise FileValidationError(
            f"File exceeds the {settings.max_file_size_mb} MB size limit: {path.name}"
        )
    if not os.access(resolved, os.R_OK):
        raise FileValidationError(f"File is not readable: {path.name}")
    return resolved


def wait_for_stable_file(path: Path, settings: Settings) -> None:
    """Wait until size and mtime stop changing; raises FileNotStableError."""
    deadline = time.monotonic() + settings.stable_timeout_seconds
    last_signature: tuple[int, int] | None = None
    stable_since: float | None = None
    while time.monotonic() < deadline:
        try:
            stat = path.stat()
            signature = (stat.st_size, stat.st_mtime_ns)
        except OSError as exc:
            raise FileNotStableError(f"File disappeared while waiting: {path.name}") from exc
        now = time.monotonic()
        if signature == last_signature:
            if stable_since is None:
                stable_since = now
            elif now - stable_since >= settings.stable_seconds:
                return
        else:
            last_signature = signature
            stable_since = now
        time.sleep(0.2)
    raise FileNotStableError(
        f"File did not become stable within {settings.stable_timeout_seconds:.0f}s: "
        f"{path.name}"
    )


def archive_copy(source: Path, processed_dir: Path, file_hash: str) -> Path:
    """Copy a processed document into the local archive without overwriting."""
    processed_dir.mkdir(parents=True, exist_ok=True)
    destination = processed_dir / f"{file_hash[:12]}_{source.name}"
    if destination.exists():
        if sha256_file(destination) == file_hash:
            return destination
        raise FileValidationError(
            f"Archive name collision for {source.name}; refusing to overwrite"
        )
    shutil.copy2(source, destination)
    return destination
