"""System health checks for the dashboard and environment script."""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from inboxshield.ai.ollama_client import OllamaClient
from inboxshield.config import Settings
from inboxshield.database import Database


def _directory_writable(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        with tempfile.NamedTemporaryFile(dir=path, prefix=".health-", delete=True):
            return True
    except OSError:
        return False


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def daemon_status(database: Database, settings: Settings) -> dict[str, Any]:
    heartbeat = database.get_heartbeat()
    if not heartbeat:
        return {"running": False, "reason": "No daemon heartbeat recorded"}
    last = _parse_iso(heartbeat.get("heartbeat_at"))
    age = (datetime.now(UTC) - last).total_seconds() if last else None
    threshold = max(30, settings.heartbeat_interval_seconds * 3)
    running = (
        heartbeat.get("status") == "RUNNING" and age is not None and age <= threshold
    )
    return {
        "running": running,
        "status": heartbeat.get("status"),
        "pid": heartbeat.get("pid"),
        "started_at": heartbeat.get("started_at"),
        "heartbeat_at": heartbeat.get("heartbeat_at"),
        "seconds_since_heartbeat": round(age, 1) if age is not None else None,
        "model": heartbeat.get("model"),
        "version": heartbeat.get("version"),
        "reason": None if running else "Heartbeat is stale or the daemon has stopped",
    }


class HealthService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        client: OllamaClient | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.client = client or OllamaClient(settings)

    def check(self, *, deep: bool = False) -> dict[str, Any]:
        ollama = self.client.model_status()
        try:
            db_ok = self.database.integrity_check()
            db_error = None
        except Exception as exc:
            db_ok = False
            db_error = str(exc)

        try:
            usage = shutil.disk_usage(self.settings.data_dir)
            disk = {
                "free_gb": round(usage.free / (1024**3), 1),
                "total_gb": round(usage.total / (1024**3), 1),
            }
        except OSError:
            disk = {"free_gb": None, "total_gb": None}

        heartbeat = daemon_status(self.database, self.settings)
        metrics = self.database.overview_metrics()
        capabilities = [str(cap) for cap in ollama.get("capabilities", [])]
        return {
            "checked_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "ollama": {
                **ollama,
                "url": self.settings.ollama_url,
                "configured_model": self.settings.model,
                "vision": "vision" in capabilities,
            },
            "database": {
                "ok": db_ok,
                "error": db_error,
                "path": str(self.settings.db_path),
                "size_bytes": self.database.db_size_bytes(),
                "integrity_checked": deep,
            },
            "daemon": heartbeat,
            "directories": {
                "incoming": {
                    "path": str(self.settings.incoming_dir),
                    "exists": self.settings.incoming_dir.is_dir(),
                    "writable": _directory_writable(self.settings.incoming_dir),
                },
                "processed": {
                    "path": str(self.settings.processed_dir),
                    "exists": self.settings.processed_dir.is_dir(),
                    "writable": _directory_writable(self.settings.processed_dir),
                },
                "logs": {
                    "path": str(self.settings.logs_dir),
                    "exists": self.settings.logs_dir.is_dir(),
                    "writable": _directory_writable(self.settings.logs_dir),
                },
            },
            "disk": disk,
            "jobs": {
                "pending": metrics["pending_jobs"],
                "failed": metrics["failed_jobs"],
            },
            "last_success": metrics["last_success"],
            "pid_file": os.getpid(),
        }
