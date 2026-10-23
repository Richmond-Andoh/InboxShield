"""Overview page."""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go
import streamlit as st

from inboxshield.config import Settings
from inboxshield.dashboard.components import (
    STATUS_LABELS,
    empty_state,
    esc,
    format_money,
    metric_row,
    page_header,
    section,
)
from inboxshield.database import Database, InvoiceFilters
from inboxshield.services.invoice_service import InvoiceService

_CHART_COLORS = ["#0f766e", "#0ea5e9", "#f59e0b", "#ef4444", "#64748b", "#8b5cf6"]
_LAYOUT = {
    "margin": {"l": 10, "r": 10, "t": 30, "b": 10},
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "font": {"family": "Arial, sans-serif", "size": 12, "color": "#334155"},
}


def _chart_layout(title: str) -> dict[str, Any]:
    return {
        **_LAYOUT,
        "title": {"text": title, "font": {"size": 13, "color": "#0f172a"}},
    }


def render(
    service: InvoiceService,
    database: Database,
    settings: Settings,
    health: dict[str, Any],
    *,
    refreshed_at: str,
) -> None:
    metrics = service.metrics()
    page_header(
        "Overview",
        "Local invoice auditing at a glance. Every figure is read from the SQLite ledger.",
    )

    ai = health.get("ollama", {})
    if ai.get("available") and ai.get("model_present"):
        ai_text = f"Local AI ready · {settings.model}"
        ai_hint = "No invoice data leaves this machine"
    elif ai.get("available"):
        ai_text = "Model unavailable"
        ai_hint = f"Run: ollama pull {settings.model}"
    else:
        ai_text = "Ollama offline"
        ai_hint = "Start with: ollama serve"
    daemon = health.get("daemon", {})
    daemon_text = "Daemon running" if daemon.get("running") else "Daemon stopped"
    daemon_hint = (
        f"Last heartbeat {daemon.get('heartbeat_at') or 'never'}"
        if daemon.get("running")
        else "Start with: python daemon.py"
    )

    metric_row(
        [
            (
                "Documents processed",
                str(metrics["total_documents"]),
                "Files observed by the daemon",
                "teal",
            ),
            (
                "Verified invoices",
                str(metrics["verified"]),
                "Logged without duplicate signals",
                "green",
            ),
            (
                "Flagged duplicates",
                str(metrics["flagged_duplicate"]),
                "High-confidence duplicate records",
                "red",
            ),
            (
                "Awaiting review",
                str(metrics["awaiting_review"]),
                f"{metrics['pending_findings']} open finding(s)",
                "amber",
            ),
        ]
    )
    st.write("")
    metric_row(
        [
            ("Local AI", ai_text, ai_hint, "slate"),
            ("Daemon", daemon_text, daemon_hint, "slate"),
            (
                "Failed jobs",
                str(metrics["failed_jobs"]),
                "See Processing Activity for details",
                "red" if metrics["failed_jobs"] else "slate",
            ),
            (
                "Last processed",
                (metrics["last_success"] or "never")[:19].replace("T", " "),
                "UTC completion timestamp",
                "slate",
            ),
        ]
    )

    if metrics["total_documents"] == 0:
        st.write("")
        empty_state(
            "No invoices have been processed yet.",
            "Start the daemon, then copy a PDF, JPG, or PNG into the incoming folder. "
            "The demo generator can create synthetic invoices for you.",
        )

    section("Audited value", "Kept separate by currency; amounts are never mixed")
    totals = metrics["totals_by_currency"]
    if totals:
        columns = st.columns(min(4, len(totals)))
        for index, total in enumerate(totals):
            with columns[index % len(columns)]:
                st.markdown(
                    f'<div class="is-card"><div class="is-metric-label">'
                    f'{esc(total["currency"])}</div><div class="is-metric-value">'
                    f'{esc(format_money(total["total_minor"]))}</div>'
                    f'<div class="is-metric-hint">{total["count"]} invoice(s)</div></div>',
                    unsafe_allow_html=True,
                )
    else:
        st.markdown(
            '<div class="is-muted">No monetary totals available yet.</div>',
            unsafe_allow_html=True,
        )

    section("Processing trends")
    left, right = st.columns(2)
    with left:
        volume = database.volume_by_day(14)
        if volume:
            figure = go.Figure(
                go.Bar(
                    x=[row["day"] for row in volume],
                    y=[row["count"] for row in volume],
                    marker_color="#0f766e",
                    hovertemplate="%{x}: %{y} invoice(s)<extra></extra>",
                )
            )
            figure.update_layout(**_chart_layout("Invoices by processing date"))
            figure.update_yaxes(gridcolor="#e2e8f0", rangemode="tozero")
            st.plotly_chart(figure, width="stretch", key="overview_volume")
        else:
            empty_state("No processing history in the last 14 days.")
    with right:
        distribution = database.status_distribution()
        if distribution:
            figure = go.Figure(
                go.Pie(
                    labels=[
                        STATUS_LABELS.get(row["status"], row["status"])
                        for row in distribution
                    ],
                    values=[row["count"] for row in distribution],
                    hole=0.55,
                    marker={"colors": _CHART_COLORS},
                    textinfo="label+value",
                )
            )
            figure.update_layout(**_chart_layout("Audit status distribution"))
            st.plotly_chart(figure, width="stretch", key="overview_status")
        else:
            empty_state("No invoice statuses recorded yet.")

    left, right = st.columns(2)
    with left:
        findings = database.findings_by_type()
        if findings:
            labels = [
                row["finding_type"].replace("_", " ").title() for row in findings
            ]
            figure = go.Figure(
                go.Bar(
                    x=[row["count"] for row in findings],
                    y=labels,
                    orientation="h",
                    marker_color=[
                        "#b91c1c" if row["risk_level"] == "HIGH" else "#b45309"
                        for row in findings
                    ],
                    hovertemplate="%{y}: %{x}<extra></extra>",
                )
            )
            figure.update_layout(**_chart_layout("Audit findings by type"))
            figure.update_xaxes(gridcolor="#e2e8f0")
            st.plotly_chart(figure, width="stretch", key="overview_findings")
        else:
            empty_state("No audit findings. That is a good sign.")
    with right:
        totals = metrics["totals_by_currency"]
        if totals:
            figure = go.Figure(
                go.Bar(
                    x=[total["currency"] for total in totals],
                    y=[total["total_minor"] / 100 for total in totals],
                    marker_color="#0ea5e9",
                    text=[
                        f"{format_money(total['total_minor'])}"
                        for total in totals
                    ],
                    textposition="auto",
                    hovertemplate="%{x}: %{y:,.2f}<extra></extra>",
                )
            )
            figure.update_layout(**_chart_layout("Invoice value by currency"))
            figure.update_yaxes(gridcolor="#e2e8f0", rangemode="tozero")
            st.plotly_chart(figure, width="stretch", key="overview_value")
        else:
            empty_state("No invoice values recorded yet.")

    section("Recent invoices", "Latest records first")
    recent, _ = service.search(InvoiceFilters(limit=8))
    if recent:
        rows = [
            {
                "ID": row["id"],
                "Vendor": row.get("vendor_original") or row.get("vendor") or "-",
                "Invoice #": row.get("invoice_number") or "-",
                "Amount": format_money(row.get("amount_minor"), row.get("currency")),
                "Status": STATUS_LABELS.get(row["status"], row["status"]),
                "Received": (row.get("timestamp") or "")[:19].replace("T", " "),
            }
            for row in recent
        ]
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        empty_state("The ledger is empty.")

    section("Recent activity")
    events = database.list_events(limit=8)
    if events:
        for event in events:
            st.markdown(
                f'<div class="is-kv"><span>{esc((event["created_at"] or "")[:19].replace("T", " "))} '
                f'· {esc(event["event_type"].replace("_", " ").title())}</span>'
                f'<span>{esc(event["message"])}</span></div>',
                unsafe_allow_html=True,
            )
    else:
        empty_state("No application events yet.")

    st.markdown(
        f'<div class="is-muted" style="margin-top:1.2rem">Last dashboard refresh: '
        f"{esc(refreshed_at)} UTC. "
        f'Auto-refresh interval: {settings.dashboard_refresh_seconds}s.</div>',
        unsafe_allow_html=True,
    )
