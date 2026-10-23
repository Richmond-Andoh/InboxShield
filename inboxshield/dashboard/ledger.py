"""Invoice ledger and detail view."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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
    preview_bytes,
    render_findings,
    section,
    status_badge,
)
from inboxshield.database import Database, InvoiceFilters
from inboxshield.services.export_service import export_invoices_csv
from inboxshield.services.invoice_service import InvoiceService

_STATUS_CHOICES = [
    "VERIFIED_LOGGED",
    "FLAGGED_DUPLICATE",
    "FLAGGED_REVIEW",
    "FAILED",
]


def _parse_amount(value: float) -> int | None:
    if value is None or value <= 0:
        return None
    return round(value * 100)


def _collect_filters(settings: Settings, *, page: int, export: bool = False) -> InvoiceFilters:
    vendor = st.session_state.get("ledger_vendor", "")
    statuses = st.session_state.get("ledger_statuses", [])
    currency = st.session_state.get("ledger_currency", "All")
    date_range = st.session_state.get("ledger_dates", ())
    min_amount = st.session_state.get("ledger_min_amount", 0.0)
    max_amount = st.session_state.get("ledger_max_amount", 0.0)
    sort = st.session_state.get("ledger_sort", "timestamp")
    date_from = date_to = None
    if isinstance(date_range, (tuple, list)) and date_range:
        if len(date_range) >= 1 and date_range[0]:
            date_from = date_range[0].isoformat()
        if len(date_range) >= 2 and date_range[1]:
            date_to = date_range[1].isoformat()
    limit = 5000 if export else settings.dashboard_page_size
    offset = 0 if export else (page - 1) * limit
    return InvoiceFilters(
        vendor=vendor or None,
        statuses=tuple(statuses or ()),
        currency=None if currency == "All" else currency,
        date_from=date_from,
        date_to=date_to,
        min_amount_minor=_parse_amount(min_amount),
        max_amount_minor=_parse_amount(max_amount),
        limit=limit,
        offset=offset,
        sort=sort,
        descending=st.session_state.get("ledger_descending", True),
    )


def _render_filters(settings: Settings, currency_options: list[str]) -> None:
    row1 = st.columns([2.2, 1.6, 1.2, 1.6])
    with row1[0]:
        st.text_input("Search vendor, invoice file", key="ledger_vendor")
    with row1[1]:
        st.multiselect(
            "Status",
            options=_STATUS_CHOICES,
            format_func=lambda value: STATUS_LABELS.get(value, value),
            key="ledger_statuses",
        )
    with row1[2]:
        st.selectbox("Currency", options=["All", *currency_options], key="ledger_currency")
    with row1[3]:
        today = datetime.now(UTC).date()
        default_start = today - timedelta(days=365)
        st.date_input(
            "Received between",
            value=(default_start, today),
            key="ledger_dates",
        )
    row2 = st.columns([1, 1, 1.3, 1.3])
    with row2[0]:
        st.number_input(
            "Min amount", min_value=0.0, value=0.0, step=50.0,
            key="ledger_min_amount", format="%.2f",
        )
    with row2[1]:
        st.number_input(
            "Max amount", min_value=0.0, value=0.0, step=50.0,
            key="ledger_max_amount", format="%.2f",
        )
    with row2[2]:
        st.selectbox(
            "Sort by",
            options=["timestamp", "amount", "vendor", "invoice_date", "id"],
            format_func=lambda value: {
                "timestamp": "Processing time",
                "amount": "Amount",
                "vendor": "Vendor",
                "invoice_date": "Invoice date",
                "id": "Invoice ID",
            }.get(value, value),
            key="ledger_sort",
        )
    with row2[3]:
        st.checkbox("Newest / largest first", value=True, key="ledger_descending")
    if st.button("Reset filters", key="ledger_reset"):
        for key in [
            "ledger_vendor",
            "ledger_statuses",
            "ledger_currency",
            "ledger_dates",
            "ledger_min_amount",
            "ledger_max_amount",
            "ledger_sort",
            "ledger_descending",
            "ledger_page",
        ]:
            st.session_state.pop(key, None)
        st.rerun()


def _render_detail(
    database: Database, settings: Settings, service: InvoiceService, invoice_id: int
) -> None:
    detail = service.get_detail(invoice_id)
    if detail is None:
        st.warning("The selected invoice no longer exists.")
        st.session_state.pop("selected_invoice_id", None)
        return
    invoice = detail["invoice"]
    display_vendor = invoice.get("vendor_original") or invoice.get("vendor") or "Unknown vendor"
    st.markdown(
        f'<div class="is-section">Invoice #{invoice["id"]} · {esc(display_vendor)} '
        f'{status_badge(invoice["status"])} '
        f'{status_badge("REVIEWED" if invoice["review_status"] == "REVIEWED" else "PENDING")}</div>',
        unsafe_allow_html=True,
    )
    tabs = st.tabs(["Extraction", "Findings & review", "Document", "Processing history"])
    with tabs[0]:
        left, right = st.columns(2)
        with left:
            kv_list(
                [
                    ("Invoice ID", invoice["id"]),
                    ("Vendor (original)", invoice.get("vendor_original") or "-"),
                    ("Vendor (normalized)", invoice.get("vendor") or "-"),
                    ("Invoice number", invoice.get("invoice_number") or "-"),
                    ("Total amount", format_money(invoice.get("amount_minor"), invoice.get("currency"))),
                    ("Currency", invoice.get("currency") or "-"),
                    ("Invoice date", invoice.get("invoice_date") or "-"),
                    ("Due date", invoice.get("due_date") or "-"),
                ]
            )
        with right:
            kv_list(
                [
                    ("Subtotal", format_money(invoice.get("subtotal_minor"), invoice.get("currency"))),
                    ("Tax", format_money(invoice.get("tax_minor"), invoice.get("currency"))),
                    ("Pages inspected", invoice.get("pages_inspected") or "-"),
                    ("Extraction confidence", "Not provided by model"),
                    ("Source file", invoice.get("filename") or "-"),
                    ("File hash", (invoice.get("file_hash") or "")[:20] + "..."),
                    ("Received", (invoice.get("timestamp") or "")[:19].replace("T", " ")),
                    ("Processing started", (invoice.get("processing_started_at") or "-")[:19].replace("T", " ")),
                ]
            )
        notes_raw = invoice.get("validation_notes")
        if notes_raw:
            try:
                notes = json.loads(notes_raw)
            except (TypeError, json.JSONDecodeError):
                notes = {"issues": [str(notes_raw)]}
            issues = notes.get("issues") or []
            if issues:
                st.markdown("**Validation notes**")
                for issue in issues:
                    st.markdown(f"- {esc(issue)}")
            else:
                st.markdown(
                    '<div class="is-muted">No validation warnings recorded.</div>',
                    unsafe_allow_html=True,
                )
        if invoice.get("error_message"):
            st.error(invoice["error_message"])

    with tabs[1]:
        render_findings(
            database,
            detail["findings"],
            key_prefix=f"ledger-{invoice_id}",
        )
        if detail["inbound_matches"]:
            section("Other invoices matched to this record")
            for finding in detail["inbound_matches"]:
                st.markdown(
                    f'<div class="is-kv"><span>Invoice #{finding["invoice_id"]} · '
                    f'{esc(finding["finding_type"].replace("_", " ").title())}</span>'
                    f'<span>{esc(finding["explanation"])}</span></div>',
                    unsafe_allow_html=True,
                )

    with tabs[2]:
        image = preview_bytes(invoice.get("file_path"), settings)
        if image:
            st.image(image, caption=invoice.get("filename"), width="stretch")
            safe_path = invoice.get("file_path") or ""
            st.markdown(
                f'<div class="is-muted is-mono">Local archive: {esc(safe_path)}</div>',
                unsafe_allow_html=True,
            )
        else:
            empty_state(
                "Preview unavailable.",
                "The document is missing from the local archive or is not a supported image/PDF. "
                "No arbitrary filesystem paths are rendered.",
            )

    with tabs[3]:
        jobs = database.list_jobs_for_invoice(invoice_id)
        if jobs:
            for job in jobs:
                kv_list(
                    [
                        ("Job", job["id"]),
                        ("State", job["state"]),
                        ("Attempts", job["attempts"]),
                        ("Started", (job.get("started_at") or "-")[:19].replace("T", " ")),
                        ("Completed", (job.get("completed_at") or "-")[:19].replace("T", " ")),
                        ("Last error", job.get("last_error") or "-"),
                    ]
                )
        else:
            st.markdown(
                '<div class="is-muted">No processing job linked to this invoice.</div>',
                unsafe_allow_html=True,
            )
        section("Invoice events")
        events = database.list_events(limit=50, invoice_id=invoice_id)
        if events:
            for event in events:
                st.markdown(
                    f'<div class="is-kv"><span>{(event["created_at"] or "")[:19].replace("T", " ")} '
                    f'· {esc(event["event_type"].replace("_", " ").title())}</span>'
                    f'<span>{esc(event["message"])}</span></div>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown('<div class="is-muted">No events recorded.</div>', unsafe_allow_html=True)


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
        "Invoice Ledger",
        "Search, filter, and inspect every audited financial document.",
    )
    currencies = sorted(
        {
            row["currency"]
            for row in database.list_invoices(limit=1000)
            if row.get("currency")
        }
    )
    _render_filters(settings, currencies)
    st.session_state.setdefault("ledger_page", 1)
    filters = _collect_filters(settings, page=int(st.session_state["ledger_page"]))
    rows, total = service.search(filters)
    pages = max(1, (total + settings.dashboard_page_size - 1) // settings.dashboard_page_size)
    if int(st.session_state["ledger_page"]) > pages:
        st.session_state["ledger_page"] = pages

    st.markdown(
        f'<div class="is-muted">{total} invoice(s) match. Showing page '
        f'{min(int(st.session_state["ledger_page"]), pages)} of {pages}.</div>',
        unsafe_allow_html=True,
    )

    if not rows:
        empty_state(
            "No invoices match these filters.",
            "Clear the filters or place a new document in the incoming folder.",
        )
        return

    table_rows = [
        {
            "ID": row["id"],
            "Vendor": row.get("vendor_original") or row.get("vendor") or "-",
            "Invoice #": row.get("invoice_number") or "-",
            "Amount": (row.get("amount_minor") or 0) / 100 if row.get("amount_minor") is not None else None,
            "Currency": row.get("currency") or "-",
            "Invoice date": row.get("invoice_date") or "-",
            "Status": STATUS_LABELS.get(row["status"], row["status"]),
            "Received": (row.get("timestamp") or "")[:19].replace("T", " "),
            "Source file": row.get("filename") or "-",
        }
        for row in rows
    ]
    event = st.dataframe(
        table_rows,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key="ledger_table",
        column_config={
            "ID": st.column_config.NumberColumn(width="small"),
            "Amount": st.column_config.NumberColumn(format="%.2f"),
            "Vendor": st.column_config.TextColumn(width="medium"),
            "Source file": st.column_config.TextColumn(width="medium"),
        },
    )
    selection = getattr(event, "selection", None)
    selected_rows = getattr(selection, "rows", []) if selection is not None else []
    if selected_rows:
        index = selected_rows[0]
        if 0 <= index < len(rows):
            st.session_state["selected_invoice_id"] = int(rows[index]["id"])

    nav = st.columns([1, 1, 2, 1, 1])
    with nav[0]:
        if st.button("Previous", disabled=int(st.session_state["ledger_page"]) <= 1, key="ledger_prev"):
            st.session_state["ledger_page"] = max(1, int(st.session_state["ledger_page"]) - 1)
            st.rerun()
    with nav[1]:
        if st.button("Next", disabled=int(st.session_state["ledger_page"]) >= pages, key="ledger_next"):
            st.session_state["ledger_page"] = min(pages, int(st.session_state["ledger_page"]) + 1)
            st.rerun()
    with nav[4]:
        export_filters = _collect_filters(settings, page=1, export=True)
        csv_data, export_count = export_invoices_csv(database, export_filters)
        st.download_button(
            "Export filtered CSV",
            data=csv_data,
            file_name="inboxshield_invoices.csv",
            mime="text/csv",
            key="ledger_export",
        )
        st.caption(f"{export_count} row(s)")

    selected_id = st.session_state.get("selected_invoice_id")
    if selected_id:
        st.divider()
        _render_detail(database, settings, service, int(selected_id))
    else:
        st.info("Select a row to open the invoice detail view.")
