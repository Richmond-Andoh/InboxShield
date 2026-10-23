"""Structured application logging with secret redaction."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key)\s*([=:])\s*\S+"
)
_SECRET_BEARER = re.compile(r"(?i)(bearer)\s+[A-Za-z0-9._\-]+")

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def redact_secrets(text: str) -> str:
    """Remove likely credentials from log-bound text."""
    redacted = _SECRET_ASSIGNMENT.sub(r"\1\2***", text)
    redacted = _SECRET_BEARER.sub(r"\1 ***", redacted)
    return redacted


def sanitize_error(error: BaseException | str, limit: int = 400) -> str:
    """Return a short, credential-free description safe for storage/display."""
    text = str(error) if not isinstance(error, BaseException) else f"{type(error).__name__}: {error}"
    text = redact_secrets(text).replace("\n", " ").strip()
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def configure_logging(
    level: str = "INFO",
    log_file: Path | None = None,
    *,
    console: bool = True,
) -> None:
    """Configure the root logger once, with rotation and redaction filter."""
    root = logging.getLogger()
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(numeric_level)

    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(_FORMAT)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(numeric_level)
        root.addHandler(console_handler)

    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(numeric_level)
            root.addHandler(file_handler)
        except OSError:
            logging.getLogger(__name__).warning(
                "Could not open log file %s; continuing with console logging", log_file
            )

    logging.getLogger("watchdog").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
