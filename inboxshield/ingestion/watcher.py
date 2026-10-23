"""Watchdog-based monitoring of the incoming directory."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from inboxshield.config import Settings
from inboxshield.ingestion.file_utils import is_hidden_or_temporary

logger = logging.getLogger(__name__)


class _InvoiceEventHandler(FileSystemEventHandler):
    def __init__(
        self,
        settings: Settings,
        enqueue: Callable[[Path], None],
    ) -> None:
        super().__init__()
        self.settings = settings
        self.enqueue = enqueue

    def _consider(self, raw_path: str | bytes | None) -> None:
        if not raw_path:
            return
        path = Path(raw_path)
        if is_hidden_or_temporary(path):
            return
        if path.suffix.lower() not in self.settings.supported_extensions:
            return
        if not path.is_file():
            return
        self.enqueue(path)

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._consider(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._consider(getattr(event, "dest_path", event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._consider(event.src_path)


class InvoiceWatcher:
    def __init__(self, settings: Settings, enqueue: Callable[[Path], None]) -> None:
        self.settings = settings
        self.handler = _InvoiceEventHandler(settings, enqueue)
        self.observer = Observer()
        self._started = False

    def start(self) -> None:
        if not self.settings.incoming_dir.is_dir():
            self.settings.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.observer.schedule(
            self.handler, str(self.settings.incoming_dir), recursive=False
        )
        self.observer.start()
        self._started = True
        logger.info("Watching %s for new documents", self.settings.incoming_dir)

    def stop(self) -> None:
        if self._started:
            self.observer.stop()
            self.observer.join(timeout=5)
            self._started = False
            logger.info("File watcher stopped")

    @property
    def running(self) -> bool:
        return self._started and self.observer.is_alive()
