#!/usr/bin/env python3
"""Reset InboxShield demo state without deleting non-demo documents.

By default this is a dry run. With --yes it:
  1. Moves DEMO-* files from incoming/processed folders into data/reset_archive/.
  2. Deletes the SQLite database (and WAL/SHM sidecars).

Non-demo invoice files are never deleted. Stop the daemon before running this.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inboxshield.config import get_settings
from inboxshield.database import Database
from inboxshield.services.health_service import daemon_status


def _demo_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return [
        path
        for path in directory.glob("DEMO-*")
        if path.is_file() and path.name.startswith("DEMO-")
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="Apply the reset")
    parser.add_argument(
        "--keep-documents",
        action="store_true",
        help="Only reset the database; leave demo documents where they are",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Proceed even if the daemon heartbeat looks active",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    settings.ensure_directories()
    database = Database(settings.db_path)
    if settings.db_path.exists():
        try:
            status = daemon_status(database, settings)
        except Exception:
            status = {"running": False}
        if status.get("running") and not args.force:
            print(
                "A running InboxShield daemon was detected. Stop it first, or pass "
                "--force if you are sure the heartbeat is stale."
            )
            return 1

    files = [] if args.keep_documents else (
        _demo_files(settings.incoming_dir) + _demo_files(settings.processed_dir)
    )
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_root = settings.data_dir / "reset_archive" / timestamp
    db_files = [
        settings.db_path,
        Path(f"{settings.db_path}-wal"),
        Path(f"{settings.db_path}-shm"),
    ]

    print("InboxShield demo reset")
    print(f"  Database: {settings.db_path} ({'exists' if settings.db_path.exists() else 'missing'})")
    print(f"  Demo documents to archive: {len(files)}")
    for path in files:
        print(f"    - {path}")
    if not args.yes:
        print("\nDry run only. Re-run with --yes to apply.")
        return 0

    moved = 0
    for source in files:
        parent = "incoming_invoices" if source.parent == settings.incoming_dir else "processed_invoices"
        destination = archive_root / parent / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination = destination.with_name(
                f"{destination.stem}-{timestamp}{destination.suffix}"
            )
        shutil.move(str(source), str(destination))
        moved += 1
    if moved:
        print(f"Archived {moved} demo document(s) under {archive_root}")

    for path in db_files:
        try:
            os.remove(path)
            print(f"Removed {path}")
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"Could not remove {path}: {exc}")
            return 1

    print("\nReset complete. Initialize and restart the daemon when ready:")
    print("  python -m inboxshield.database --init")
    print("  python daemon.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
