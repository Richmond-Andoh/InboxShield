"""Central configuration for InboxShield AI.

Environment variables (and an optional project-root ``.env`` file) are the
single source of truth for runtime configuration.  Nothing sensitive is stored
in the database.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SUPPORTED_EXTENSIONS: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".pdf")
DEFAULT_CURRENCIES: tuple[str, ...] = (
    "GHS",
    "USD",
    "EUR",
    "GBP",
    "NGN",
    "ZAR",
    "KES",
    "XOF",
    "CAD",
    "AUD",
    "INR",
    "CNY",
    "JPY",
    "CHF",
    "AED",
)


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader that never overrides real environment variables."""
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _env_float(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Immutable, validated runtime settings."""

    home: Path = PROJECT_ROOT
    incoming_dir: Path = PROJECT_ROOT / "incoming_invoices"
    processed_dir: Path = PROJECT_ROOT / "processed_invoices"
    data_dir: Path = PROJECT_ROOT / "data"
    logs_dir: Path = PROJECT_ROOT / "logs"
    db_path: Path = PROJECT_ROOT / "data" / "inboxshield.db"

    ollama_url: str = "http://localhost:11434"
    model: str = "gemma4:e2b"
    ollama_timeout_seconds: float = 600.0
    inference_temperature: float = 0.0
    max_pdf_pages: int = 3

    supported_extensions: tuple[str, ...] = SUPPORTED_EXTENSIONS
    supported_currencies: tuple[str, ...] = DEFAULT_CURRENCIES
    max_file_size_mb: int = 20
    max_image_dimension: int = 1800
    pdf_render_dpi: int = 150

    stable_seconds: float = 1.5
    stable_timeout_seconds: float = 30.0
    max_retries: int = 3
    retry_backoff_seconds: float = 5.0
    worker_count: int = 1
    reconcile_interval_seconds: int = 60
    heartbeat_interval_seconds: int = 10

    duplicate_date_window_days: int = 30
    vendor_similarity_threshold: float = 0.86
    amount_match_tolerance_minor: int = 0
    duplicate_currency: str = "GHS"

    dashboard_refresh_seconds: int = 5
    dashboard_page_size: int = 25
    archive_processed_files: bool = True

    log_level: str = "INFO"
    log_file: Path = PROJECT_ROOT / "logs" / "inboxshield.log"
    environment: str = "production"
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    def ensure_directories(self) -> None:
        for path in (
            self.incoming_dir,
            self.processed_dir,
            self.data_dir,
            self.logs_dir,
            self.log_file.parent,
            self.db_path.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def validate(self) -> None:
        if not self.model or ":" not in self.model:
            raise ValueError(
                "INBOXSHIELD_MODEL must be an Ollama model identifier such as "
                "'gemma4:e2b'"
            )
        if not self.ollama_url.startswith(("http://", "https://")):
            raise ValueError("INBOXSHIELD_OLLAMA_URL must be an http(s) URL")
        if self.worker_count < 1:
            raise ValueError("INBOXSHIELD_WORKERS must be >= 1")
        if not 0 < self.dashboard_refresh_seconds <= 3600:
            raise ValueError("Dashboard refresh interval must be between 1 and 3600s")
        if not 0.0 < self.vendor_similarity_threshold <= 1.0:
            raise ValueError("Vendor similarity threshold must be in (0, 1]")

    @classmethod
    def from_env(cls, home: Path | None = None) -> Settings:
        base = Path(home).resolve() if home else PROJECT_ROOT
        _load_dotenv(base / ".env")
        incoming = Path(_env_str("INBOXSHIELD_INCOMING_DIR", str(base / "incoming_invoices")))
        processed = Path(
            _env_str("INBOXSHIELD_PROCESSED_DIR", str(base / "processed_invoices"))
        )
        data = Path(_env_str("INBOXSHIELD_DATA_DIR", str(base / "data")))
        logs = Path(_env_str("INBOXSHIELD_LOG_DIR", str(base / "logs")))
        currencies = tuple(
            code.strip().upper()
            for code in _env_str(
                "INBOXSHIELD_SUPPORTED_CURRENCIES", ",".join(DEFAULT_CURRENCIES)
            ).split(",")
            if code.strip()
        )
        extensions = tuple(
            ext if ext.startswith(".") else f".{ext}"
            for ext in (
                part.strip().lower()
                for part in _env_str(
                    "INBOXSHIELD_SUPPORTED_EXTENSIONS", ",".join(SUPPORTED_EXTENSIONS)
                ).split(",")
            )
            if ext
        )
        settings = cls(
            home=base,
            incoming_dir=incoming.resolve(),
            processed_dir=processed.resolve(),
            data_dir=data.resolve(),
            logs_dir=logs.resolve(),
            db_path=Path(
                _env_str("INBOXSHIELD_DB_PATH", str(data / "inboxshield.db"))
            ).resolve(),
            ollama_url=_env_str("INBOXSHIELD_OLLAMA_URL", "http://localhost:11434").rstrip(
                "/"
            ),
            model=_env_str("INBOXSHIELD_MODEL", "gemma4:e2b"),
            ollama_timeout_seconds=_env_float("INBOXSHIELD_OLLAMA_TIMEOUT", 600.0, 5.0),
            inference_temperature=_env_float("INBOXSHIELD_TEMPERATURE", 0.0, 0.0),
            max_pdf_pages=_env_int("INBOXSHIELD_MAX_PDF_PAGES", 3, 1),
            supported_extensions=extensions or SUPPORTED_EXTENSIONS,
            supported_currencies=currencies or DEFAULT_CURRENCIES,
            max_file_size_mb=_env_int("INBOXSHIELD_MAX_FILE_SIZE_MB", 20, 1),
            max_image_dimension=_env_int("INBOXSHIELD_MAX_IMAGE_DIMENSION", 1800, 512),
            pdf_render_dpi=_env_int("INBOXSHIELD_PDF_DPI", 150, 72),
            stable_seconds=_env_float("INBOXSHIELD_STABLE_SECONDS", 1.5, 0.1),
            stable_timeout_seconds=_env_float(
                "INBOXSHIELD_STABLE_TIMEOUT", 30.0, 1.0
            ),
            max_retries=_env_int("INBOXSHIELD_MAX_RETRIES", 3, 0),
            retry_backoff_seconds=_env_float("INBOXSHIELD_RETRY_BACKOFF", 5.0, 0.0),
            worker_count=_env_int("INBOXSHIELD_WORKERS", 1, 1),
            reconcile_interval_seconds=_env_int(
                "INBOXSHIELD_RECONCILE_INTERVAL", 60, 5
            ),
            heartbeat_interval_seconds=_env_int(
                "INBOXSHIELD_HEARTBEAT_INTERVAL", 10, 2
            ),
            duplicate_date_window_days=_env_int(
                "INBOXSHIELD_DUPLICATE_WINDOW_DAYS", 30, 0
            ),
            vendor_similarity_threshold=_env_float(
                "INBOXSHIELD_VENDOR_SIMILARITY", 0.86, 0.1
            ),
            amount_match_tolerance_minor=_env_int(
                "INBOXSHIELD_AMOUNT_TOLERANCE_MINOR", 0, 0
            ),
            duplicate_currency=_env_str("INBOXSHIELD_BASE_CURRENCY", "GHS").upper(),
            dashboard_refresh_seconds=_env_int(
                "INBOXSHIELD_DASHBOARD_REFRESH", 5, 1
            ),
            dashboard_page_size=_env_int("INBOXSHIELD_PAGE_SIZE", 25, 5),
            archive_processed_files=_env_bool("INBOXSHIELD_ARCHIVE_FILES", True),
            log_level=_env_str("INBOXSHIELD_LOG_LEVEL", "INFO").upper(),
            log_file=Path(
                _env_str("INBOXSHIELD_LOG_FILE", str(logs / "inboxshield.log"))
            ).resolve(),
            environment=_env_str("INBOXSHIELD_ENV", "production"),
        )
        settings.validate()
        return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings.from_env()
    settings.ensure_directories()
    return settings


def clear_settings_cache() -> None:
    get_settings.cache_clear()
