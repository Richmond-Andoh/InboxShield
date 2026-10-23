#!/usr/bin/env python3
"""Verify the local InboxShield AI runtime before a demo.

Checks Python, dependencies, SQLite, directories, Ollama, and the configured
vision model. Does not download model weights unless --pull-model is passed.
"""

from __future__ import annotations

import argparse
import importlib
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inboxshield.config import get_settings
from inboxshield.database import Database
from inboxshield.services.health_service import HealthService

REQUIRED_MODULES = [
    "pydantic",
    "PIL",
    "pymupdf",
    "watchdog",
    "httpx",
    "streamlit",
    "pandas",
    "plotly",
    "pytest",
]


def _line(label: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {label}" + (f" - {detail}" if detail else ""))
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when Ollama or the configured model is unavailable",
    )
    parser.add_argument(
        "--pull-model",
        action="store_true",
        help="Explicitly pull the configured model with 'ollama pull'",
    )
    args = parser.parse_args(argv)

    print("InboxShield AI environment check")
    print("=" * 42)
    failures = 0
    python_ok = sys.version_info >= (3, 11)
    failures += 0 if _line("Python >= 3.11", python_ok, sys.version.split()[0]) else 1
    sqlite_ok = sqlite3.sqlite_version_info >= (3, 35)
    failures += 0 if _line("SQLite >= 3.35", sqlite_ok, sqlite3.sqlite_version) else 1

    for module in REQUIRED_MODULES:
        try:
            imported = importlib.import_module(module)
            version = getattr(imported, "__version__", "installed")
            _line(f"Dependency {module}", True, str(version))
        except Exception as exc:
            failures += 1
            _line(f"Dependency {module}", False, str(exc))

    try:
        settings = get_settings()
        settings.validate()
        settings.ensure_directories()
        _line("Configuration", True, f"model={settings.model}")
    except Exception as exc:
        print(f"[FAIL] Configuration - {exc}")
        return 1

    database = Database(settings.db_path)
    try:
        database.initialize(settings)
        integrity = database.integrity_check()
        failures += 0 if _line("Database", integrity, str(settings.db_path)) else 1
    except Exception as exc:
        failures += 1
        _line("Database", False, str(exc))

    for name, path in [
        ("Incoming directory", settings.incoming_dir),
        ("Processed directory", settings.processed_dir),
        ("Log directory", settings.logs_dir),
        ("Data directory", settings.data_dir),
    ]:
        writable = path.is_dir() and os.access(path, os.W_OK)
        failures += 0 if _line(name, writable, str(path)) else 1

    usage = shutil.disk_usage(settings.data_dir)
    free_gb = usage.free / (1024**3)
    failures += 0 if _line("Disk space", free_gb >= 2, f"{free_gb:.1f} GB free") else 1

    if args.pull_model:
        binary = shutil.which("ollama")
        if not binary:
            print("[FAIL] Ollama CLI not found; install Ollama first")
            return 1
        print(f"Explicitly pulling {settings.model}; this downloads model weights...")
        result = subprocess.run([binary, "pull", settings.model], check=False)
        failures += 0 if result.returncode == 0 else 1

    health = HealthService(database, settings).check(deep=True)
    ai = health["ollama"]
    failures += 0 if _line("Ollama service", ai.get("available", False), settings.ollama_url) else 1
    if ai.get("available"):
        failures += 0 if _line(
            "Configured model", ai.get("model_present", False), settings.model
        ) else 1
        capabilities = ai.get("capabilities") or []
        vision = "vision" in capabilities
        _line("Vision capability", vision, ", ".join(capabilities) or "not reported")
        if not vision:
            print(
                f"      Pull a vision-capable model, for example: ollama pull {settings.model}"
            )
        if not ai.get("model_present"):
            print(f"      Run: ollama pull {settings.model}")

    daemon = health["daemon"]
    _line("Daemon heartbeat", bool(daemon.get("running")), daemon.get("reason") or "live")

    print("=" * 42)
    blockers = failures if args.strict else 0
    if args.strict and (not ai.get("available") or not ai.get("model_present")):
        blockers = max(blockers, 1)
    if blockers:
        print(f"Environment check found {blockers} blocking issue(s).")
        return 1
    print("Environment looks ready for local processing.")
    print(
        "\nNext steps:\n"
        "  python -m inboxshield.database --init\n"
        "  python daemon.py\n"
        "  streamlit run app.py\n"
        "  python scripts/generate_demo_invoices.py --clean\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
