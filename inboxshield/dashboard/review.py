"""Review queue page."""

from __future__ import annotations

import json
from typing import Any

import streamlit as st

from inboxshield.config import Settings
from inboxshield.dashboard.components import (
    STATUS_LABELS,
    empty_state,
    esc,
    format_money,
    page_header,
    render_findings,
    status_badge,
)
from inboxshield.database import Database
from inboxshield.services.invoice_service import InvoiceService


def render(
    service: InvoiceService,
    database: Database,
    settings: Settings,
    health: dict[str, Any],
    *,
    refreshed_at: str,
) -> None:
    del settings, refreshed_at
    page_header(
        "Review Queue",
        "Invoices that need a human decision before they should be trusted.",
    )
    queue = service.pending_queue(limit=200)
    failed_jobs = database.list_jobs(limit=20, state="FAILED")
    metrics = service.metrics()

    cols = st.columns(4)
    cols[0].metric("Invoices in queue", len(queue))
    cols[1].metric("Open findings", metrics["pending_findings"])
    cols[2].metric("Failed jobs", metrics["failed_jobs"])
    cols[3].metric(
        "Duplicate flags", metrics["flagged_duplicate"]
    )
    st.write("")

    if not queue:
        empty_state(
            "The review queue is clear.",
            "New duplicate signals or incomplete extractions will appear here.",
        )
    for invoice in queue:
        vendor = invoice.get("vendor_original") or invoice.get("vendor") or "Unknown vendor"
        amount = format_money(invoice.get("amount_minor"), invoice.get("currency"))
        title = f"#{invoice['id']} · {vendor} · {amount} · {STATUS_LABELS.get(invoice['status'], invoice['status'])}"
        with st.expander(title, expanded=False):
            st.markdown(
                f'{status_badge(invoice["status"])} '
                f'{status_badge("REVIEWED" if invoice["review_status"] == "REVIEWED" else "PENDING")}',
                unsafe_allow_html=True,
            )
            findings = database.list_findings(
                invoice_id=int(invoice["id"]), include_matched=True
            )
            notes_raw = invoice.get("validation_notes")
            if notes_raw:
                try:
                    notes = json.loads(notes_raw)
                except (TypeError, json.JSONDecodeError):
                    notes = {}
                issues = notes.get("issues") or []
                missing = notes.get("missing_fields") or []
                if missing:
                    st.markdown(f"**Missing fields:** {esc(', '.join(missing))}")
                if issues:
                    st.markdown("**Validation warnings**")
                    for issue in issues:
                        st.markdown(f"- {esc(issue)}")
            if findings:
                render_findings(
                    database,
                    findings,
                    key_prefix=f"review-{invoice['id']}",
                )
            else:
                st.markdown(
                    '<div class="is-muted">No findings attached; review the extracted '
                    "values in the ledger.</div>",
                    unsafe_allow_html=True,
                )
            if st.button(
                "Open invoice in ledger",
                key=f"review-open-{invoice['id']}",
            ):
                st.session_state["selected_invoice_id"] = int(invoice["id"])
                st.session_state["nav"] = "Invoice Ledger"
                st.rerun()

    if failed_jobs:
        st.divider()
        st.markdown('<div class="is-section">Failed processing jobs</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="is-muted">These files did not produce an invoice. Check the '
            "local model, file integrity, and the daemon log.</div>",
            unsafe_allow_html=True,
        )
        for job in failed_jobs:
            st.markdown(
                f'<div class="is-finding high"><div class="is-finding-title">'
                f'{esc(job["file_path"].split("/")[-1])}</div>'
                f'<div class="is-finding-body">{esc(job.get("last_error") or "Unknown error")}</div>'
                f'<div class="is-finding-meta">Attempts: {job["attempts"]} · '
                f'{esc((job.get("created_at") or "")[:19].replace("T", " "))}</div></div>',
                unsafe_allow_html=True,
            )
