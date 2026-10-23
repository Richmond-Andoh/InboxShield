#!/usr/bin/env python3
"""InboxShield AI background daemon.

Watches ``incoming_invoices``, processes documents with local Gemma through
Ollama, audits duplicates, and persists results in SQLite.
"""

from __future__ import annotations

import argparse
import contextlib
import heapq
import logging
import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path

from inboxshield import __version__
from inboxshield.ai.extractor import GemmaExtractor
from inboxshield.ai.ollama_client import OllamaClient
from inboxshield.config import Settings, get_settings
from inboxshield.database import Database
from inboxshield.ingestion.processor import InvoiceProcessor
from inboxshield.ingestion.reconciliation import discover_pending_files
from inboxshield.ingestion.watcher import InvoiceWatcher
from inboxshield.models import EventType, JobState, ProcessResult
from inboxshield.services.health_service import daemon_status
from inboxshield.utils.logging_config import configure_logging

logger = logging.getLogger("inboxshield.daemon")


class InvoiceDaemon:
    def __init__(self, settings: Settings, *, allow_degraded: bool = False) -> None:
        self.settings = settings
        self.allow_degraded = allow_degraded
        self.database = Database(settings.db_path)
        self.client = OllamaClient(settings)
        self.extractor = GemmaExtractor(settings, self.client)
        self.processor = InvoiceProcessor(settings, self.database, self.extractor)
        self.task_queue: queue.Queue[Path | None] = queue.Queue()
        self.stop_event = threading.Event()
        self._queued_paths: set[str] = set()
        self._queue_lock = threading.Lock()
        self._retry_heap: list[tuple[float, str, Path]] = []
        self._retry_condition = threading.Condition()
        self._threads: list[threading.Thread] = []
        self.watcher = InvoiceWatcher(settings, self.enqueue)
        self._summary_lock = threading.Lock()
        self._counts = {"completed": 0, "duplicate": 0, "failed": 0, "skipped": 0}

    # --------------------------------------------------------------- lifecycle
    def preflight(self) -> bool:
        self.settings.ensure_directories()
        self.database.initialize(self.settings)
        self.database.reset_interrupted_jobs()

        heartbeat = daemon_status(self.database, self.settings)
        if heartbeat.get("running"):
            logger.error(
                "Another InboxShield daemon appears to be running (pid %s). "
                "Stop it or pass --force if you are certain it is stale.",
                heartbeat.get("pid"),
            )
            return False

        status = self.client.model_status()
        if not status.get("available"):
            logger.error("Ollama is not reachable at %s", self.settings.ollama_url)
            logger.error("Start it with: ollama serve")
            logger.error("Error: %s", status.get("error"))
            return self.allow_degraded
        if not status.get("model_present"):
            logger.error(
                "Configured model '%s' is not installed. Run: ollama pull %s",
                self.settings.model,
                self.settings.model,
            )
            return self.allow_degraded
        capabilities = status.get("capabilities") or []
        if capabilities and "vision" not in capabilities:
            logger.warning(
                "Model '%s' does not advertise vision support; document "
                "extraction will likely fail",
                self.settings.model,
            )
        logger.info(
            "Local AI ready: model=%s capabilities=%s",
            self.settings.model,
            ",".join(capabilities) if capabilities else "unknown",
        )
        return True

    def enqueue(self, path: Path) -> None:
        key = str(path)
        with self._queue_lock:
            if key in self._queued_paths:
                return
            self._queued_paths.add(key)
        self.task_queue.put(path)
        logger.debug("Queued %s", path.name)

    def _release(self, path: Path) -> None:
        with self._queue_lock:
            self._queued_paths.discard(str(path))

    def _schedule_retry(self, path: Path, attempts: int) -> None:
        if attempts >= self.settings.max_retries:
            logger.error(
                "Not retrying %s: retry limit reached (%d)",
                path.name,
                self.settings.max_retries,
            )
            return
        delay = min(
            self.settings.retry_backoff_seconds * max(1, attempts),
            300.0,
        )
        due = time.monotonic() + delay
        with self._retry_condition:
            heapq.heappush(self._retry_heap, (due, str(path), path))
            self._retry_condition.notify_all()
        logger.warning("Retrying %s in %.1fs (attempt %d)", path.name, delay, attempts + 1)

    def _retry_worker(self) -> None:
        while not self.stop_event.is_set():
            with self._retry_condition:
                now = time.monotonic()
                while self._retry_heap and self._retry_heap[0][0] <= now:
                    _, _, path = heapq.heappop(self._retry_heap)
                    self.enqueue(path)
                if self._retry_heap:
                    timeout = max(0.2, self._retry_heap[0][0] - time.monotonic())
                else:
                    timeout = 1.0
                self._retry_condition.wait(timeout=timeout)

    def _worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                path = self.task_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if path is None:
                self.task_queue.task_done()
                break
            try:
                result = self.processor.process_file(path)
                with self._summary_lock:
                    if result.state == JobState.COMPLETED:
                        self._counts["completed"] += 1
                    elif result.state == JobState.SKIPPED_DUPLICATE:
                        self._counts["duplicate"] += 1
                    elif result.state == JobState.FAILED:
                        self._counts["failed"] += 1
                    else:
                        self._counts["skipped"] += 1
                if result.retryable:
                    job = None
                    try:
                        from inboxshield.ingestion.file_utils import sha256_file

                        job = self.database.get_job_by_hash(sha256_file(path))
                    except OSError:
                        pass
                    attempts = int(job["attempts"]) if job else 1
                    self._schedule_retry(path, attempts)
            except Exception:
                logger.exception("Unexpected error while processing %s", path.name)
                with self._summary_lock:
                    self._counts["failed"] += 1
            finally:
                self._release(path)
                self.task_queue.task_done()

    def _heartbeat_worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.database.heartbeat()
            except Exception:
                logger.exception("Could not update daemon heartbeat")
            self.stop_event.wait(self.settings.heartbeat_interval_seconds)

    def _reconcile_worker(self) -> None:
        while not self.stop_event.is_set():
            if self.stop_event.wait(self.settings.reconcile_interval_seconds):
                break
            try:
                pending = discover_pending_files(self.settings, self.database)
                if pending:
                    logger.info("Reconciliation found %d file(s)", len(pending))
                    self.database.add_event(
                        EventType.RECONCILED,
                        f"Reconciliation queued {len(pending)} unprocessed file(s)",
                    )
                    for path in pending:
                        self.enqueue(path)
            except Exception:
                logger.exception("Reconciliation pass failed")

    def _install_signal_handlers(self) -> None:
        def handle(signum: int, _frame: object) -> None:
            logger.info("Received signal %s; shutting down gracefully", signum)
            self.stop_event.set()
            with self._retry_condition:
                self._retry_condition.notify_all()

        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(ValueError):
                signal.signal(sig, handle)

    # --------------------------------------------------------------------- run
    def run(self) -> int:
        if not self.preflight():
            return 2
        self._install_signal_handlers()
        self.database.start_heartbeat(os.getpid(), self.settings.model, __version__)
        self.database.add_event(
            EventType.DAEMON_STARTED,
            f"Daemon started (pid {os.getpid()}, model {self.settings.model})",
        )
        self.watcher.start()

        for index in range(self.settings.worker_count):
            thread = threading.Thread(
                target=self._worker, name=f"invoice-worker-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)
        retry_thread = threading.Thread(
            target=self._retry_worker, name="retry-scheduler", daemon=True
        )
        retry_thread.start()
        self._threads.append(retry_thread)
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_worker, name="heartbeat", daemon=True
        )
        heartbeat_thread.start()
        self._threads.append(heartbeat_thread)
        reconcile_thread = threading.Thread(
            target=self._reconcile_worker, name="reconciliation", daemon=True
        )
        reconcile_thread.start()
        self._threads.append(reconcile_thread)

        pending = discover_pending_files(self.settings, self.database)
        if pending:
            logger.info("Startup reconciliation found %d file(s)", len(pending))
            self.database.add_event(
                EventType.RECONCILED,
                f"Startup reconciliation queued {len(pending)} file(s)",
            )
            for path in pending:
                self.enqueue(path)

        logger.info(
            "Daemon running: watched=%s db=%s workers=%d",
            self.settings.incoming_dir,
            self.settings.db_path,
            self.settings.worker_count,
        )
        print(
            f"InboxShield AI daemon running. Watching {self.settings.incoming_dir}\n"
            f"Model: {self.settings.model}   Database: {self.settings.db_path}\n"
            "Press Ctrl+C to stop."
        )
        try:
            while not self.stop_event.wait(1.0):
                pass
        finally:
            self.shutdown()
        return 0

    def shutdown(self) -> None:
        self.stop_event.set()
        self.watcher.stop()
        for _ in self._threads[: self.settings.worker_count]:
            self.task_queue.put(None)
        for thread in self._threads:
            thread.join(timeout=5)
        try:
            self.database.stop_heartbeat()
            self.database.add_event(
                EventType.DAEMON_STOPPED,
                f"Daemon stopped ({self._counts})",
            )
        except Exception:
            logger.exception("Could not finalize daemon state")
        logger.info("Daemon stopped. Summary: %s", self._counts)

    def run_once(self) -> int:
        if not self.preflight():
            return 2
        self.database.start_heartbeat(os.getpid(), self.settings.model, __version__)
        files = discover_pending_files(self.settings, self.database)
        if not files:
            logger.info("No pending files found in %s", self.settings.incoming_dir)
        results: list[ProcessResult] = []
        for path in files:
            result = self.processor.process_file(path)
            results.append(result)
        self.database.stop_heartbeat()
        failed = sum(1 for result in results if result.state == JobState.FAILED)
        logger.info(
            "One-pass complete: %d file(s), %d failed", len(results), failed
        )
        for result in results:
            print(f" - {result.state.value}: {result.message}")
        return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="InboxShield AI daemon")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process currently pending files once, then exit",
    )
    parser.add_argument(
        "--allow-degraded",
        action="store_true",
        help="Start even if Ollama or the configured model is unavailable",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Start even if a recent daemon heartbeat exists",
    )
    parser.add_argument(
        "--model", help="Override the configured Ollama model for this run"
    )
    parser.add_argument(
        "--incoming", help="Override the watched directory for this run"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    if args.model or args.incoming:
        overrides = {}
        if args.model:
            overrides["model"] = args.model
        if args.incoming:
            overrides["incoming_dir"] = Path(args.incoming).resolve()
        settings = Settings(**{**settings.__dict__, **overrides})
        settings.validate()
        settings.ensure_directories()
    configure_logging(
        settings.log_level,
        settings.log_file,
        console=True,
    )
    logger.info("InboxShield AI %s starting", __version__)
    if args.force:
        daemon = InvoiceDaemon(settings, allow_degraded=args.allow_degraded)
        try:
            daemon.database.initialize(settings)
            daemon.database.stop_heartbeat()
        except Exception:
            logger.debug("Could not reset stale daemon heartbeat", exc_info=True)
    daemon = InvoiceDaemon(settings, allow_degraded=args.allow_degraded)
    try:
        return daemon.run_once() if args.once else daemon.run()
    except KeyboardInterrupt:
        daemon.stop_event.set()
        daemon.shutdown()
        return 130


if __name__ == "__main__":
    sys.exit(main())
