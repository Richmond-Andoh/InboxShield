"""SQLite persistence layer.

A new connection is opened per operation (cheap for SQLite) so connections are
never shared across threads.  Writes use explicit ``BEGIN IMMEDIATE``
transactions and every statement is parameterized.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from inboxshield.config import Settings
from inboxshield.models import (
    EventType,
    FindingDraft,
    FindingType,
    InvoiceCandidate,
    InvoiceStatus,
    JobState,
    ReviewStatus,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    vendor TEXT,
    vendor_original TEXT,
    invoice_number TEXT,
    amount TEXT,
    amount_minor INTEGER,
    currency TEXT,
    invoice_date TEXT,
    due_date TEXT,
    subtotal TEXT,
    subtotal_minor INTEGER,
    tax_amount TEXT,
    tax_minor INTEGER,
    status TEXT NOT NULL CHECK (status IN (
        'QUEUED','PROCESSING','VERIFIED_LOGGED','FLAGGED_DUPLICATE',
        'FLAGGED_REVIEW','FAILED'
    )),
    review_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (review_status IN ('PENDING','REVIEWED')),
    validation_notes TEXT,
    pages_inspected INTEGER,
    confidence REAL,
    processing_started_at TEXT,
    timestamp TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error_message TEXT,
    notes TEXT,
    CHECK (amount_minor IS NULL OR amount_minor >= 0)
);

CREATE INDEX IF NOT EXISTS idx_invoices_vendor ON invoices(vendor);
CREATE INDEX IF NOT EXISTS idx_invoices_number ON invoices(invoice_number);
CREATE INDEX IF NOT EXISTS idx_invoices_amount ON invoices(amount_minor);
CREATE INDEX IF NOT EXISTS idx_invoices_currency ON invoices(currency);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_invoices_timestamp ON invoices(timestamp);

CREATE TABLE IF NOT EXISTS audit_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    finding_type TEXT NOT NULL,
    risk_level TEXT NOT NULL CHECK (risk_level IN ('LOW','MEDIUM','HIGH')),
    explanation TEXT NOT NULL,
    matched_invoice_id INTEGER REFERENCES invoices(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (review_status IN ('PENDING','REVIEWED')),
    reviewer_note TEXT,
    reviewed_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_findings_unique
    ON audit_findings(invoice_id, finding_type, IFNULL(matched_invoice_id, -1));
CREATE INDEX IF NOT EXISTS idx_findings_invoice ON audit_findings(invoice_id);
CREATE INDEX IF NOT EXISTS idx_findings_matched ON audit_findings(matched_invoice_id);
CREATE INDEX IF NOT EXISTS idx_findings_type ON audit_findings(finding_type);
CREATE INDEX IF NOT EXISTS idx_findings_review ON audit_findings(review_status);

CREATE TABLE IF NOT EXISTS processing_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN (
        'QUEUED','PROCESSING','COMPLETED','SKIPPED_DUPLICATE','FAILED'
    )),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    invoice_id INTEGER REFERENCES invoices(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_state ON processing_jobs(state);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON processing_jobs(created_at);

CREATE TABLE IF NOT EXISTS app_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'INFO',
    message TEXT NOT NULL,
    invoice_id INTEGER REFERENCES invoices(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_created ON app_events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_type ON app_events(event_type);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daemon_heartbeat (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    pid INTEGER,
    status TEXT NOT NULL,
    started_at TEXT,
    heartbeat_at TEXT,
    model TEXT,
    version TEXT
);
"""


def utcnow() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass
class InvoiceFilters:
    vendor: str | None = None
    statuses: Sequence[str] = field(default_factory=tuple)
    currency: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    min_amount_minor: int | None = None
    max_amount_minor: int | None = None
    limit: int = 25
    offset: int = 0
    sort: str = "timestamp"
    descending: bool = True


_SORT_COLUMNS = {
    "id": "id",
    "vendor": "vendor",
    "amount": "amount_minor",
    "invoice_date": "invoice_date",
    "timestamp": "timestamp",
    "status": "status",
}


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    # ------------------------------------------------------------------ core
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as connection:
            connection.isolation_level = None
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def initialize(self, settings: Settings | None = None) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version < 1:
                connection.executescript(_SCHEMA_V1)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                connection.commit()
            elif version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema version {version} is newer than supported "
                    f"{SCHEMA_VERSION}; refusing to open"
                )
        if settings is not None:
            self.seed_settings(settings)

    def integrity_check(self) -> bool:
        with self.connect() as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
            return bool(row and row[0] == "ok")

    def seed_settings(self, settings: Settings) -> None:
        values = {
            "model": settings.model,
            "watched_directory": str(settings.incoming_dir),
            "max_file_size_mb": str(settings.max_file_size_mb),
            "supported_extensions": ",".join(settings.supported_extensions),
            "duplicate_date_window_days": str(settings.duplicate_date_window_days),
            "vendor_similarity_threshold": str(settings.vendor_similarity_threshold),
            "dashboard_refresh_seconds": str(settings.dashboard_refresh_seconds),
            "base_currency": settings.duplicate_currency,
        }
        with self.transaction() as connection:
            for key, value in values.items():
                connection.execute(
                    "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO NOTHING",
                    (key, value, utcnow()),
                )

    # --------------------------------------------------------------- invoices
    def insert_invoice(
        self,
        values: dict[str, Any],
        connection: sqlite3.Connection | None = None,
    ) -> int:
        now = utcnow()
        columns = [
            "filename",
            "file_path",
            "file_hash",
            "vendor",
            "vendor_original",
            "invoice_number",
            "amount",
            "amount_minor",
            "currency",
            "invoice_date",
            "due_date",
            "subtotal",
            "subtotal_minor",
            "tax_amount",
            "tax_minor",
            "status",
            "review_status",
            "validation_notes",
            "pages_inspected",
            "confidence",
            "processing_started_at",
            "timestamp",
            "updated_at",
            "error_message",
            "notes",
        ]
        payload = {column: values.get(column) for column in columns}
        payload["timestamp"] = values.get("timestamp") or now
        payload["updated_at"] = now
        if not payload["status"]:
            payload["status"] = InvoiceStatus.FLAGGED_REVIEW.value
        if not payload["review_status"]:
            payload["review_status"] = ReviewStatus.REVIEWED.value
        if payload["validation_notes"] is not None and not isinstance(
            payload["validation_notes"], str
        ):
            payload["validation_notes"] = json.dumps(payload["validation_notes"])
        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT INTO invoices({', '.join(columns)}) VALUES ({placeholders})"  # noqa: S608
        params = tuple(payload[column] for column in columns)
        if connection is not None:
            cursor = connection.execute(sql, params)
            return int(cursor.lastrowid)
        with self.transaction() as own:
            cursor = own.execute(sql, params)
            return int(cursor.lastrowid)

    def update_invoice(self, invoice_id: int, **fields: Any) -> None:
        allowed = {
            "status",
            "review_status",
            "error_message",
            "notes",
            "validation_notes",
            "amount",
            "amount_minor",
            "vendor",
            "currency",
            "invoice_date",
            "due_date",
            "pages_inspected",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        updates["updated_at"] = utcnow()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = [*updates.values(), invoice_id]
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE invoices SET {assignments} WHERE id = ?",  # noqa: S608
                values,
            )

    def get_invoice(self, invoice_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM invoices WHERE id = ?", (invoice_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_invoice_by_hash(self, file_hash: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM invoices WHERE file_hash = ?", (file_hash,)
            ).fetchone()
        return dict(row) if row else None

    def _invoice_where(self, filters: InvoiceFilters) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if filters.vendor:
            clauses.append("(vendor LIKE ? OR vendor_original LIKE ? OR filename LIKE ?)")
            pattern = f"%{filters.vendor.strip()}%"
            params.extend([pattern, pattern, pattern])
        if filters.statuses:
            placeholders = ", ".join("?" for _ in filters.statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(filters.statuses)
        if filters.currency:
            clauses.append("currency = ?")
            params.append(filters.currency)
        if filters.date_from:
            clauses.append("date(timestamp) >= date(?)")
            params.append(filters.date_from)
        if filters.date_to:
            clauses.append("date(timestamp) <= date(?)")
            params.append(filters.date_to)
        if filters.min_amount_minor is not None:
            clauses.append("amount_minor >= ?")
            params.append(filters.min_amount_minor)
        if filters.max_amount_minor is not None:
            clauses.append("amount_minor <= ?")
            params.append(filters.max_amount_minor)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    def search_invoices(
        self, filters: InvoiceFilters
    ) -> tuple[list[dict[str, Any]], int]:
        where, params = self._invoice_where(filters)
        sort_column = _SORT_COLUMNS.get(filters.sort, "timestamp")
        direction = "DESC" if filters.descending else "ASC"
        limit = max(1, min(filters.limit, 500))
        offset = max(0, filters.offset)
        with self.connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM invoices{where}",  # noqa: S608
                    params,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"SELECT * FROM invoices{where} "  # noqa: S608
                f"ORDER BY {sort_column} {direction}, id DESC "
                "LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [dict(row) for row in rows], total

    def list_invoices(
        self, limit: int = 50, statuses: Sequence[str] | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM invoices"
        params: list[Any] = []
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            sql += f" WHERE status IN ({placeholders})"
            params.extend(statuses)
        sql += " ORDER BY timestamp DESC, id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def invoice_candidates(
        self,
        exclude_id: int | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> list[InvoiceCandidate]:
        sql = (
            "SELECT id, vendor, vendor_original, invoice_number, amount_minor, "
            "currency, invoice_date, filename FROM invoices WHERE status != 'FAILED'"
        )
        params: list[Any] = []
        if exclude_id is not None:
            sql += " AND id != ?"
            params.append(exclude_id)
        if connection is None:
            with self.connect() as own:
                rows = own.execute(sql, params).fetchall()
        else:
            rows = connection.execute(sql, params).fetchall()
        from inboxshield.utils.text import normalize_invoice_number, normalize_vendor

        return [
            InvoiceCandidate(
                id=int(row["id"]),
                vendor=row["vendor"],
                vendor_normalized=normalize_vendor(row["vendor"] or row["vendor_original"]),
                invoice_number=row["invoice_number"],
                invoice_number_normalized=normalize_invoice_number(row["invoice_number"]),
                amount_minor=row["amount_minor"],
                currency=row["currency"],
                invoice_date=row["invoice_date"],
                filename=row["filename"],
            )
            for row in rows
        ]

    # ---------------------------------------------------------------- findings
    def add_finding(
        self,
        invoice_id: int,
        finding: FindingDraft,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        sql = (
            "INSERT INTO audit_findings(invoice_id, finding_type, risk_level, "
            "explanation, matched_invoice_id, created_at, review_status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'PENDING') "
            "ON CONFLICT(invoice_id, finding_type, IFNULL(matched_invoice_id, -1)) "
            "DO NOTHING"
        )
        params = (
            invoice_id,
            finding.finding_type.value,
            finding.risk_level.value,
            finding.explanation,
            finding.matched_invoice_id,
            utcnow(),
        )
        if connection is not None:
            cursor = connection.execute(sql, params)
            if cursor.rowcount:
                connection.execute(
                    "UPDATE invoices SET review_status = 'PENDING' WHERE id = ?",
                    (invoice_id,),
                )
            return int(cursor.lastrowid or 0)
        with self.transaction() as own:
            cursor = own.execute(sql, params)
            if cursor.rowcount:
                own.execute(
                    "UPDATE invoices SET review_status = 'PENDING' WHERE id = ?",
                    (invoice_id,),
                )
            return int(cursor.lastrowid or 0)

    def list_findings(
        self,
        *,
        invoice_id: int | None = None,
        duplicate_only: bool = False,
        pending_only: bool = False,
        include_matched: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if invoice_id is not None:
            clauses.append("f.invoice_id = ?")
            params.append(invoice_id)
        if duplicate_only:
            placeholders = ", ".join(
                "?"
                for _ in (
                    FindingType.EXACT_FILE_DUPLICATE,
                    FindingType.STRONG_INVOICE_DUPLICATE,
                    FindingType.POTENTIAL_DUPLICATE,
                    FindingType.SIMILAR_VENDOR_MATCH,
                    FindingType.INVOICE_NUMBER_CONFLICT,
                )
            )
            clauses.append(f"f.finding_type IN ({placeholders})")
            params.extend(
                [
                    FindingType.EXACT_FILE_DUPLICATE.value,
                    FindingType.STRONG_INVOICE_DUPLICATE.value,
                    FindingType.POTENTIAL_DUPLICATE.value,
                    FindingType.SIMILAR_VENDOR_MATCH.value,
                    FindingType.INVOICE_NUMBER_CONFLICT.value,
                ]
            )
        if pending_only:
            clauses.append("f.review_status = 'PENDING'")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        select = "SELECT f.*, i.vendor AS invoice_vendor, i.filename AS invoice_filename"
        join = " FROM audit_findings f JOIN invoices i ON i.id = f.invoice_id"
        if include_matched:
            select += (
                ", m.vendor AS matched_vendor, m.filename AS matched_filename, "
                "m.amount AS matched_amount, m.currency AS matched_currency, "
                "m.invoice_number AS matched_invoice_number, "
                "m.invoice_date AS matched_invoice_date, m.status AS matched_status"
            )
            join += " LEFT JOIN invoices m ON m.id = f.matched_invoice_id"
        sql = f"{select}{join}{where} ORDER BY f.created_at DESC, f.id DESC LIMIT ? OFFSET ?"
        with self.connect() as connection:
            rows = connection.execute(sql, [*params, limit, offset]).fetchall()
        return [dict(row) for row in rows]

    def get_finding(self, finding_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM audit_findings WHERE id = ?", (finding_id,)
            ).fetchone()
        return dict(row) if row else None

    def review_finding(
        self, finding_id: int, note: str, review_status: str = ReviewStatus.REVIEWED
    ) -> bool:
        now = utcnow()
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE audit_findings SET review_status = ?, reviewer_note = ?, "
                "reviewed_at = ? WHERE id = ?",
                (review_status, note.strip()[:2000], now, finding_id),
            )
            if cursor.rowcount == 0:
                return False
            row = connection.execute(
                "SELECT invoice_id FROM audit_findings WHERE id = ?", (finding_id,)
            ).fetchone()
            invoice_id = int(row["invoice_id"]) if row else None
            if invoice_id is not None:
                pending = connection.execute(
                    "SELECT COUNT(*) FROM audit_findings WHERE invoice_id = ? "
                    "AND review_status = 'PENDING'",
                    (invoice_id,),
                ).fetchone()[0]
                new_status = "REVIEWED" if pending == 0 else "PENDING"
                connection.execute(
                    "UPDATE invoices SET review_status = ?, updated_at = ? WHERE id = ?",
                    (new_status, now, invoice_id),
                )
            connection.execute(
                "INSERT INTO app_events(event_type, level, message, invoice_id, created_at) "
                "VALUES (?, 'INFO', ?, ?, ?)",
                (
                    EventType.REVIEW_UPDATED.value,
                    f"Finding #{finding_id} marked {review_status}"
                    + (f": {note.strip()[:300]}" if note.strip() else ""),
                    invoice_id,
                    now,
                ),
            )
        return True

    # -------------------------------------------------------------------- jobs
    def get_job_by_hash(self, file_hash: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM processing_jobs WHERE file_hash = ?", (file_hash,)
            ).fetchone()
        return dict(row) if row else None

    def create_job(self, file_path: str, file_hash: str) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO processing_jobs(file_path, file_hash, state, attempts, "
                "created_at) VALUES (?, ?, 'QUEUED', 0, ?) "
                "ON CONFLICT(file_hash) DO NOTHING",
                (file_path, file_hash, utcnow()),
            )
            if cursor.rowcount == 1 and cursor.lastrowid:
                return int(cursor.lastrowid)
            row = connection.execute(
                "SELECT id FROM processing_jobs WHERE file_hash = ?", (file_hash,)
            ).fetchone()
        return int(row["id"]) if row else 0

    def update_job(
        self,
        job_id: int,
        *,
        state: JobState,
        error: str | None = None,
        invoice_id: int | None = None,
        increment_attempts: bool = False,
        started: bool = False,
        completed: bool = False,
    ) -> None:
        now = utcnow()
        assignments = ["state = ?", "last_error = ?"]
        params: list[Any] = [state.value, error]
        if increment_attempts:
            assignments.append("attempts = attempts + 1")
        if started:
            assignments.append("started_at = ?")
            params.append(now)
        if completed:
            assignments.append("completed_at = ?")
            params.append(now)
        if invoice_id is not None:
            assignments.append("invoice_id = ?")
            params.append(invoice_id)
        params.append(job_id)
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE processing_jobs SET {', '.join(assignments)} "  # noqa: S608
                "WHERE id = ?",
                params,
            )

    def reset_interrupted_jobs(self) -> int:
        """Mark jobs left PROCESSING by a crash as failed so they can be retried."""
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE processing_jobs SET state = 'FAILED', "
                "last_error = 'Processing was interrupted by a daemon shutdown' "
                "WHERE state = 'PROCESSING'"
            )
            return int(cursor.rowcount)

    def list_jobs(
        self, limit: int = 100, offset: int = 0, state: str | None = None
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT j.*, i.filename AS invoice_filename FROM processing_jobs j "
            "LEFT JOIN invoices i ON i.id = j.invoice_id"
        )
        params: list[Any] = []
        if state:
            sql += " WHERE j.state = ?"
            params.append(state)
        sql += " ORDER BY j.created_at DESC, j.id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def list_jobs_for_invoice(self, invoice_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM processing_jobs WHERE invoice_id = ? "
                "ORDER BY created_at DESC",
                (invoice_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_jobs(self, state: str | None = None) -> int:
        with self.connect() as connection:
            if state:
                row = connection.execute(
                    "SELECT COUNT(*) FROM processing_jobs WHERE state = ?", (state,)
                ).fetchone()
            else:
                row = connection.execute("SELECT COUNT(*) FROM processing_jobs").fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------ events
    def add_event(
        self,
        event_type: EventType | str,
        message: str,
        *,
        invoice_id: int | None = None,
        level: str = "INFO",
        connection: sqlite3.Connection | None = None,
    ) -> None:
        value = event_type.value if isinstance(event_type, EventType) else event_type
        params = (value, level, message[:2000], invoice_id, utcnow())
        if connection is not None:
            connection.execute(
                "INSERT INTO app_events(event_type, level, message, invoice_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                params,
            )
            return
        with self.transaction() as own:
            own.execute(
                "INSERT INTO app_events(event_type, level, message, invoice_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                params,
            )

    def list_events(
        self,
        limit: int = 100,
        offset: int = 0,
        event_type: str | None = None,
        invoice_id: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if invoice_id is not None:
            clauses.append("invoice_id = ?")
            params.append(invoice_id)
        sql = "SELECT * FROM app_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    # ---------------------------------------------------------------- settings
    def get_setting(self, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return str(row["value"]) if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, value, utcnow()),
            )

    def list_settings(self) -> dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}

    # --------------------------------------------------------------- heartbeat
    def start_heartbeat(self, pid: int, model: str, version: str) -> None:
        now = utcnow()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO daemon_heartbeat(id, pid, status, started_at, heartbeat_at, "
                "model, version) VALUES (1, ?, 'RUNNING', ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET pid = excluded.pid, status = 'RUNNING', "
                "started_at = excluded.started_at, heartbeat_at = excluded.heartbeat_at, "
                "model = excluded.model, version = excluded.version",
                (pid, now, now, model, version),
            )

    def heartbeat(self) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE daemon_heartbeat SET heartbeat_at = ?, status = 'RUNNING' WHERE id = 1",
                (utcnow(),),
            )

    def stop_heartbeat(self) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE daemon_heartbeat SET status = 'STOPPED', heartbeat_at = ? WHERE id = 1",
                (utcnow(),),
            )

    def get_heartbeat(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM daemon_heartbeat WHERE id = 1"
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------- stats
    def overview_metrics(self) -> dict[str, Any]:
        with self.connect() as connection:
            status_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM invoices GROUP BY status"
            ).fetchall()
            status_counts = {row["status"]: int(row["count"]) for row in status_rows}
            total_documents = int(
                connection.execute("SELECT COUNT(*) FROM processing_jobs").fetchone()[0]
            )
            pending_findings = int(
                connection.execute(
                    "SELECT COUNT(*) FROM audit_findings WHERE review_status = 'PENDING'"
                ).fetchone()[0]
            )
            awaiting_review = int(
                connection.execute(
                    "SELECT COUNT(*) FROM invoices WHERE review_status = 'PENDING'"
                ).fetchone()[0]
            )
            pending_jobs = int(
                connection.execute(
                    "SELECT COUNT(*) FROM processing_jobs WHERE state IN "
                    "('QUEUED','PROCESSING')"
                ).fetchone()[0]
            )
            failed_jobs = int(
                connection.execute(
                    "SELECT COUNT(*) FROM processing_jobs WHERE state = 'FAILED'"
                ).fetchone()[0]
            )
            duplicate_findings = int(
                connection.execute(
                    "SELECT COUNT(*) FROM audit_findings WHERE finding_type IN "
                    "('EXACT_FILE_DUPLICATE','STRONG_INVOICE_DUPLICATE',"
                    "'POTENTIAL_DUPLICATE','SIMILAR_VENDOR_MATCH',"
                    "'INVOICE_NUMBER_CONFLICT')"
                ).fetchone()[0]
            )
            totals = connection.execute(
                "SELECT currency, SUM(amount_minor) AS total_minor, COUNT(*) AS count "
                "FROM invoices WHERE amount_minor IS NOT NULL AND currency IS NOT NULL "
                "GROUP BY currency ORDER BY currency"
            ).fetchall()
            last_success = connection.execute(
                "SELECT MAX(completed_at) FROM processing_jobs WHERE state IN "
                "('COMPLETED','SKIPPED_DUPLICATE')"
            ).fetchone()[0]
            last_invoice = connection.execute(
                "SELECT MAX(timestamp) FROM invoices"
            ).fetchone()[0]
        return {
            "total_documents": total_documents,
            "total_invoices": sum(status_counts.values()),
            "status_counts": status_counts,
            "verified": status_counts.get(InvoiceStatus.VERIFIED_LOGGED.value, 0),
            "flagged_duplicate": status_counts.get(
                InvoiceStatus.FLAGGED_DUPLICATE.value, 0
            ),
            "flagged_review": status_counts.get(InvoiceStatus.FLAGGED_REVIEW.value, 0),
            "failed": status_counts.get(InvoiceStatus.FAILED.value, 0),
            "pending_findings": pending_findings,
            "awaiting_review": awaiting_review,
            "duplicate_findings": duplicate_findings,
            "pending_jobs": pending_jobs,
            "failed_jobs": failed_jobs,
            "totals_by_currency": [
                {
                    "currency": row["currency"],
                    "total_minor": int(row["total_minor"] or 0),
                    "count": int(row["count"]),
                }
                for row in totals
            ],
            "last_success": last_success,
            "last_invoice": last_invoice,
        }

    def status_distribution(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM invoices GROUP BY status "
                "ORDER BY count DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def findings_by_type(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT finding_type, risk_level, COUNT(*) AS count FROM audit_findings "
                "GROUP BY finding_type, risk_level ORDER BY count DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def volume_by_day(self, days: int = 14) -> list[dict[str, Any]]:
        start = (datetime.now(UTC) - timedelta(days=days - 1)).date().isoformat()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT date(timestamp) AS day, COUNT(*) AS count FROM invoices "
                "WHERE date(timestamp) >= ? GROUP BY day ORDER BY day",
                (start,),
            ).fetchall()
        return [dict(row) for row in rows]

    def pending_review_invoices(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM invoices WHERE review_status = 'PENDING' "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def invoices_for_finding(self, finding_id: int) -> dict[str, Any] | None:
        finding = self.get_finding(finding_id)
        if finding is None:
            return None
        invoice = self.get_invoice(int(finding["invoice_id"]))
        matched = (
            self.get_invoice(int(finding["matched_invoice_id"]))
            if finding.get("matched_invoice_id")
            else None
        )
        return {"finding": finding, "invoice": invoice, "matched": matched}

    def db_size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def pending_findings_for_invoice(self, invoice_id: int) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM audit_findings WHERE invoice_id = ? "
                "AND review_status = 'PENDING'",
                (invoice_id,),
            ).fetchone()
        return int(row[0]) if row else 0


def _default_settings() -> Settings:
    return Settings.from_env()


def main() -> None:
    """``python -m inboxshield.database --init`` entry point."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="InboxShield AI database utility")
    parser.add_argument("--init", action="store_true", help="Initialize/migrate the database")
    parser.add_argument("--check", action="store_true", help="Run an integrity check")
    args = parser.parse_args()
    if not args.init and not args.check:
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    settings = _default_settings()
    settings.ensure_directories()
    database = Database(settings.db_path)
    if args.init:
        database.initialize(settings)
        print(f"Database initialized at {settings.db_path}")
    if args.check:
        ok = database.integrity_check()
        print(f"Integrity check: {'ok' if ok else 'FAILED'}")
        if not ok:
            sys.exit(2)


if __name__ == "__main__":
    main()
