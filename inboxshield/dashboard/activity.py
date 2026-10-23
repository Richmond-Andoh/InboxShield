"""Processing activity page."""

from __future__ import annotations

from typing import Any

import streamlit as st

from inboxshield.config import Settings
from inboxshield.dashboard.components import (
    empty_state,
    esc,
    page_header,
    section,
)
from inboxshield.database import Database
from inboxshield.services.invoice_service import InvoiceService

_EVENT_TYPES = [
    "FILE_DETECTED",
    "FILE_REJECTED",
    "EXTRACTION_STARTED",
    "EXTRACTION_COMPLETED",
    "DUPLICATE_CANDIDATE",
    "INVOICE_SAVED",
    "PROCESSING_FAILED",
    "REVIEW_UPDATED",
    "DAEMON_STARTED",
    "DAEMON_STOPPED",
    "HEALTH_WARNING",
    "RECONCILED",
]


def render(
    service: InvoiceService,
    database: Database,
    settings: Settings,
    health: dict[str, Any],
    *,
    refreshed_at: str,
) -> None:
    del service
    page_header(
        "Processing Activity",
        "A sanitized audit trail of ingestion, inference, auditing, and review actions.",
    )
    metrics = database.overview_metrics()
    cols = st.columns(4)
    cols[0].metric("Pending jobs", metrics["pending_jobs"])
    cols[1].metric("Failed jobs", metrics["failed_jobs"])
    cols[2].metric("Files processed", metrics["total_documents"])
    cols[3].metric("Last success", (metrics["last_success"] or "never")[:19].replace("T", " "))

    section("Event log")
    left, right = st.columns([2, 1])
    with left:
        event_filter = st.selectbox(
            "Filter event type",
            options=["All", *_EVENT_TYPES],
            format_func=lambda value: value if value == "All" else value.replace("_", " ").title(),
            key="activity_event_filter",
        )
    with right:
        limit = st.selectbox("Rows", options=[50, 100, 250], index=1, key="activity_limit")
    events = database.list_events(
        limit=int(limit),
        event_type=None if event_filter == "All" else event_filter,
    )
    if not events:
        empty_state("No events match the current filter.")
    for event in events:
        tone = {"ERROR": "red", "WARNING": "amber"}.get(event["level"], "teal")
        kind = event["event_type"].replace("_", " ").title()
        st.markdown(
            f'<div class="is-finding {tone}">'
            f'<div class="is-finding-title">{esc(kind)} '
            f'<span class="is-muted">{esc((event["created_at"] or "")[:19].replace("T", " "))}</span></div>'
            f'<div class="is-finding-body">{esc(event["message"])}</div>'
            f'<div class="is-finding-meta">Level: {esc(event["level"])}'
            + (f" · Invoice #{event['invoice_id']}" if event.get("invoice_id") else "")
            + "</div></div>",
            unsafe_allow_html=True,
        )

    section("Processing jobs")
    state = st.selectbox(
        "Job state",
        options=["All", "QUEUED", "PROCESSING", "COMPLETED", "SKIPPED_DUPLICATE", "FAILED"],
        key="activity_job_state",
    )
    jobs = database.list_jobs(limit=200, state=None if state == "All" else state)
    if not jobs:
        empty_state("No processing jobs match the current filter.")
        return
    rows = [
        {
            "Job": job["id"],
            "File": (job.get("file_path") or "").split("/")[-1],
            "State": job["state"],
            "Attempts": job["attempts"],
            "Invoice": job.get("invoice_id") or "-",
            "Started": (job.get("started_at") or "-")[:19].replace("T", " "),
            "Completed": (job.get("completed_at") or "-")[:19].replace("T", " "),
            "Last error": job.get("last_error") or "",
        }
        for job in jobs
    ]
    st.dataframe(rows, hide_index=True, width="stretch")
    st.markdown(
        f'<div class="is-muted" style="margin-top:.7rem">Database: {esc(settings.db_path)} · '
        f'Dashboard refresh: {esc(refreshed_at)} UTC</div>',
        unsafe_allow_html=True,
    )
    _ = health
