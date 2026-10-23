#!/usr/bin/env python3
"""InboxShield AI Streamlit dashboard.

Run with: streamlit run app.py
The dashboard binds to localhost by default and never performs inference.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import streamlit as st

from inboxshield import __version__
from inboxshield.config import Settings, get_settings
from inboxshield.dashboard import activity, duplicates, ledger, overview, review, settings_page
from inboxshield.dashboard.components import esc
from inboxshield.dashboard.styles import inject_css
from inboxshield.database import Database
from inboxshield.services.health_service import HealthService
from inboxshield.services.invoice_service import InvoiceService

PAGES = [
    "Overview",
    "Invoice Ledger",
    "Duplicate Detection",
    "Review Queue",
    "Processing Activity",
    "Settings & System Health",
]

PAGE_RENDERERS = {
    "Overview": overview.render,
    "Invoice Ledger": ledger.render,
    "Duplicate Detection": duplicates.render,
    "Review Queue": review.render,
    "Processing Activity": activity.render,
    "Settings & System Health": settings_page.render,
}


@st.cache_data(ttl=20, show_spinner=False)
def _load_health(
    db_path: str,
    model: str,
    ollama_url: str,
    timeout: float,
) -> dict[str, Any]:
    settings = get_settings()
    database = Database(db_path)
    return HealthService(database, settings).check()


def _bootstrap() -> tuple[Settings, Database, dict[str, Any]]:
    settings = get_settings()
    database = Database(settings.db_path)
    try:
        database.initialize(settings)
    except Exception as exc:
        st.error(
            "The SQLite database could not be initialized. "
            f"Check permissions for {settings.db_path}."
        )
        st.exception(exc)
        st.stop()
    try:
        health = _load_health(
            str(settings.db_path),
            settings.model,
            settings.ollama_url,
            settings.ollama_timeout_seconds,
        )
    except Exception as exc:
        health = {
            "ollama": {
                "available": False,
                "model_present": False,
                "capabilities": [],
                "error": str(exc),
                "url": settings.ollama_url,
            },
            "daemon": {"running": False, "reason": "Health check failed"},
            "database": {"ok": False, "size_bytes": 0, "path": str(settings.db_path)},
            "directories": {},
            "disk": {},
            "jobs": {"pending": 0, "failed": 0},
            "last_success": None,
        }
    return settings, database, health


def _sidebar(settings: Settings, database: Database, health: dict[str, Any]) -> tuple[str, bool, int]:
    st.sidebar.markdown(
        '<div class="is-brand">'
        '<span class="is-brand-mark">IS</span>'
        '<span class="is-brand-name">InboxShield AI</span>'
        '<div class="is-brand-sub">Local invoice audit agent</div>'
        "</div>",
        unsafe_allow_html=True,
    )
    page = st.sidebar.radio(
        "Navigation",
        PAGES,
        key="nav",
        label_visibility="collapsed",
    )
    daemon = health.get("daemon", {})
    ai = health.get("ollama", {})
    status_line = "Daemon running" if daemon.get("running") else "Daemon stopped"
    ai_line = (
        f"Model ready · {esc(settings.model)}"
        if ai.get("available") and ai.get("model_present")
        else ("Ollama offline" if not ai.get("available") else "Model unavailable")
    )
    heartbeat = daemon.get("heartbeat_at") or "no heartbeat"
    st.sidebar.markdown(
        '<div class="is-side-block">'
        f"<strong>{status_line}</strong><br>"
        f"{ai_line}<br>"
        f'<span style="color:#93a8bd">Last heartbeat: {esc((heartbeat or "")[:19].replace("T", " "))}</span>'
        "</div>",
        unsafe_allow_html=True,
    )
    live = st.sidebar.toggle("Live updates", value=True, key="live_updates")
    if not live and st.sidebar.button("Refresh now", key="manual_refresh"):
        st.rerun()
    stored = database.get_setting("dashboard_refresh_seconds")
    try:
        interval = int(stored) if stored else settings.dashboard_refresh_seconds
    except ValueError:
        interval = settings.dashboard_refresh_seconds
    st.sidebar.caption(
        f"v{__version__} · refresh {interval}s · localhost only"
    )
    return page, live, interval


def main() -> None:
    st.set_page_config(
        page_title="InboxShield AI",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_css()
    settings, database, health = _bootstrap()
    page, live, interval = _sidebar(settings, database, health)
    service = InvoiceService(database, settings)
    refreshed_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

    def render_page() -> None:
        PAGE_RENDERERS[page](
            service,
            database,
            settings,
            health,
            refreshed_at=refreshed_at,
        )

    if live:
        st.fragment(run_every=f"{interval}s")(render_page)()
    else:
        render_page()

    st.sidebar.markdown(
        '<div style="margin-top:2rem;font-size:.72rem;color:#7c93aa;line-height:1.5">'
        "Documents never leave this machine for inference. "
        "Duplicate findings are investigation signals, not fraud determinations."
        "</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
