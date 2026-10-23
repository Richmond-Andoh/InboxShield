"""Duplicate investigation page."""

from __future__ import annotations

from datetime import date
from typing import Any

import streamlit as st

from inboxshield.config import Settings
from inboxshield.dashboard.components import (
    STATUS_LABELS,
    empty_state,
    esc,
    format_money,
    kv_list,
    page_header,
    render_findings,
    section,
    status_badge,
)
from inboxshield.database import Database
from inboxshield.services.export_service import export_findings_csv
from inboxshield.services.invoice_service import InvoiceService


def _date_gap(left: str | None, right: str | None) -> str:
    try:
        a = date.fromisoformat(left) if left else None
        b = date.fromisoformat(right) if right else None
    except ValueError:
        return "invalid date"
    if a is None or b is None:
        return "date unavailable"
    return f"{abs((a - b).days)} day(s) apart"


def _invoice_block(invoice: dict[str, Any] | None, title: str) -> None:
    with st.container():
        st.markdown(f"**{esc(title)}**")
        if not invoice:
            st.markdown(
                '<div class="is-muted">Matched invoice record is no longer available.</div>',
                unsafe_allow_html=True,
            )
            return
        st.markdown(
            f'<div class="is-muted">Invoice #{invoice["id"]} {status_badge(invoice["status"])}</div>',
            unsafe_allow_html=True,
        )
        kv_list(
            [
                ("Vendor", invoice.get("vendor_original") or invoice.get("vendor") or "-"),
                ("Invoice number", invoice.get("invoice_number") or "missing"),
                ("Amount", format_money(invoice.get("amount_minor"), invoice.get("currency"))),
                ("Invoice date", invoice.get("invoice_date") or "missing"),
                ("Due date", invoice.get("due_date") or "-"),
                ("Source file", invoice.get("filename") or "-"),
                ("Received", (invoice.get("timestamp") or "")[:19].replace("T", " ")),
            ]
        )


def _open_in_ledger(invoice_id: int) -> None:
    st.session_state["selected_invoice_id"] = invoice_id
    st.session_state["nav"] = "Invoice Ledger"
    st.rerun()


def render(
    service: InvoiceService,
    database: Database,
    settings: Settings,
    health: dict[str, Any],
    *,
    refreshed_at: str,
) -> None:
    del health, refreshed_at
    page_header(
        "Duplicate Detection",
        "Investigation signals, not fraud verdicts. Every match is explained and reviewable.",
    )
    findings = service.duplicate_pairs(limit=500)
    pending_only = st.toggle("Show only unreviewed findings", value=True, key="dup_pending")
    if pending_only:
        findings = [item for item in findings if item["review_status"] == "PENDING"]

    total = len(findings)
    high = sum(1 for item in findings if item["risk_level"] == "HIGH")
    st.markdown(
        f'<div class="is-muted">{total} duplicate-related finding(s), '
        f"{high} high-risk. Amount and vendor matches alone are routed to review; "
        "only a matching vendor, invoice number, currency, and total is marked "
        "as a duplicate record.</div>",
        unsafe_allow_html=True,
    )

    if not findings:
        empty_state(
            "No duplicate signals match the current filter.",
            "Process more invoices or include reviewed findings.",
        )
        return

    rows = [
        {
            "Invoice": item["invoice_id"],
            "Vendor": item.get("invoice_vendor") or "-",
            "Amount": int(float(item.get("amount_minor") or 0)) / 100
            if item.get("amount_minor") is not None
            else None,
            "Currency": item.get("currency") or "-",
            "Matched": item.get("matched_invoice_id") or "-",
            "Matched amount": int(float(item.get("matched_amount") or 0)) / 100
            if item.get("matched_amount") is not None
            else None,
            "Signal": item["finding_type"].replace("_", " ").title(),
            "Risk": item["risk_level"],
            "Review": STATUS_LABELS.get(item["review_status"], item["review_status"]),
        }
        for item in findings
    ]
    event = st.dataframe(
        rows,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key="duplicates_table",
        column_config={
            "Amount": st.column_config.NumberColumn(format="%.2f"),
            "Matched amount": st.column_config.NumberColumn(format="%.2f"),
            "Vendor": st.column_config.TextColumn(width="medium"),
        },
    )
    selection = getattr(event, "selection", None)
    selected_rows = getattr(selection, "rows", []) if selection is not None else []
    if selected_rows:
        index = selected_rows[0]
        if 0 <= index < len(findings):
            st.session_state["selected_finding_id"] = int(findings[index]["id"])

    export_col, _ = st.columns([1, 3])
    with export_col:
        st.download_button(
            "Export findings CSV",
            data=export_findings_csv(database),
            file_name="inboxshield_audit_findings.csv",
            mime="text/csv",
            key="duplicates_export",
        )

    finding_id = st.session_state.get("selected_finding_id")
    if not finding_id:
        st.info("Select a finding to compare both invoices.")
        return
    finding = database.get_finding(int(finding_id))
    if finding is None:
        st.warning("The selected finding no longer exists.")
        return
    invoice = database.get_invoice(int(finding["invoice_id"]))
    matched = (
        database.get_invoice(int(finding["matched_invoice_id"]))
        if finding.get("matched_invoice_id")
        else None
    )

    st.divider()
    st.markdown(
        f'<div class="is-section">Finding #{finding["id"]} · '
        f'{esc(finding["finding_type"].replace("_", " ").title())} '
        f'<span class="is-badge {("duplicate" if finding["risk_level"] == "HIGH" else "review")}">'
        f'{esc(finding["risk_level"])} risk</span></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="is-finding-body" style="margin-bottom:1rem">'
        f'{esc(finding["explanation"])}</div>',
        unsafe_allow_html=True,
    )
    left, right = st.columns(2)
    with left:
        _invoice_block(invoice, "Selected invoice")
    with right:
        _invoice_block(matched, "Matched invoice")

    if invoice and matched:
        section("Field comparison")
        comparison = [
            (
                "Vendor",
                invoice.get("vendor_original") or invoice.get("vendor") or "-",
                matched.get("vendor_original") or matched.get("vendor") or "-",
            ),
            (
                "Invoice number",
                invoice.get("invoice_number") or "missing",
                matched.get("invoice_number") or "missing",
            ),
            (
                "Amount",
                format_money(invoice.get("amount_minor"), invoice.get("currency")),
                format_money(matched.get("amount_minor"), matched.get("currency")),
            ),
            (
                "Currency",
                invoice.get("currency") or "-",
                matched.get("currency") or "-",
            ),
            (
                "Invoice date",
                invoice.get("invoice_date") or "missing",
                matched.get("invoice_date") or "missing",
            ),
            (
                "Date gap",
                _date_gap(invoice.get("invoice_date"), matched.get("invoice_date")),
                "",
            ),
        ]
        st.dataframe(
            [
                {"Field": field, "Selected invoice": first, "Matched invoice": second}
                for field, first, second in comparison
            ],
            hide_index=True,
            width="stretch",
        )

    c1, c2 = st.columns([1, 1])
    with c1:
        if invoice and st.button("Open selected invoice in ledger", key="dup_open_primary"):
            _open_in_ledger(int(invoice["id"]))
    with c2:
        if matched and st.button("Open matched invoice in ledger", key="dup_open_matched"):
            _open_in_ledger(int(matched["id"]))

    section("Review decision")
    render_findings(
        database,
        [finding],
        key_prefix=f"duplicate-{finding['id']}",
    )
    if finding.get("review_status") == "REVIEWED":
        st.success(
            f"Reviewed {finding.get('reviewed_at') or ''} · "
            f"{finding.get('reviewer_note') or 'No note recorded'}"
        )
    st.markdown(
        '<div class="is-muted" style="margin-top:.8rem">A finding records a possible '
        "duplicate or inconsistency. It does not establish that fraud occurred, and "
        "InboxShield never approves, rejects, or pays an invoice.</div>",
        unsafe_allow_html=True,
    )
