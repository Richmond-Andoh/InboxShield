"""Settings and system health page."""

from __future__ import annotations

from typing import Any

import streamlit as st

from inboxshield import __version__
from inboxshield.config import Settings
from inboxshield.dashboard.components import (
    empty_state,
    esc,
    fmt_bytes,
    page_header,
    section,
)
from inboxshield.database import Database
from inboxshield.services.invoice_service import InvoiceService


def _dot(state: str) -> str:
    return f'<span class="is-dot {state}"></span>'


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
        "Settings & System Health",
        "Verify the local model, storage, daemon heartbeat, and privacy boundary.",
    )
    ai = health.get("ollama", {})
    daemon = health.get("daemon", {})
    db_health = health.get("database", {})
    dirs = health.get("directories", {})

    metric = st.columns(4)
    metric[0].metric("Ollama", "Online" if ai.get("available") else "Offline")
    metric[1].metric("Model", settings.model, "vision" if ai.get("vision") else "capability unknown")
    metric[2].metric("Daemon", "Running" if daemon.get("running") else "Stopped")
    metric[3].metric("Database", "Healthy" if db_health.get("ok") else "Problem")
    st.write("")

    left, right = st.columns(2)
    with left:
        section("Local AI service")
        if ai.get("available"):
            st.markdown(
                f'<div class="is-health">{_dot("ok")} Ollama reachable at '
                f'<span class="is-mono">{esc(ai.get("url"))}</span></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="is-health">{_dot("bad")} Ollama unreachable at '
                f'<span class="is-mono">{esc(ai.get("url"))}</span></div>',
                unsafe_allow_html=True,
            )
        if ai.get("available") and ai.get("model_present"):
            st.markdown(
                f'<div class="is-health">{_dot("ok")} Model <b>{esc(settings.model)}</b> installed</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="is-health">{_dot("warn")} Model <b>{esc(settings.model)}</b> not available</div>',
                unsafe_allow_html=True,
            )
        capabilities = ai.get("capabilities") or []
        st.caption(
            "Capabilities: " + (", ".join(str(c) for c in capabilities) if capabilities else "not reported")
        )
        if not ai.get("available"):
            st.markdown("**Setup**")
            st.code("ollama serve\nollama pull " + settings.model, language="bash")
            st.caption("Install Ollama, start the service, then pull the configured model.")
        elif not ai.get("model_present"):
            st.markdown("**Pull the configured model**")
            st.code(f"ollama pull {settings.model}", language="bash")
        if ai.get("available") and capabilities and "vision" not in capabilities:
            st.warning(
                "The configured model does not advertise vision support. "
                "Invoice extraction requires a vision-capable Gemma model."
            )

        section("Daemon")
        if daemon.get("running"):
            st.markdown(
                f'<div class="is-health">{_dot("ok")} Running (pid {esc(daemon.get("pid"))}), '
                f'heartbeat {esc(daemon.get("heartbeat_at"))}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="is-health">{_dot("bad")} {esc(daemon.get("reason") or "Not running")}</div>',
                unsafe_allow_html=True,
            )
            st.code("python daemon.py", language="bash")
        st.caption(
            "The dashboard never assumes the daemon is running just because the "
            "database exists; this reads a live heartbeat row."
        )

        section("Database")
        st.markdown(
            f'<div class="is-health">{_dot("ok" if db_health.get("ok") else "bad")} '
            f'SQLite integrity: {"ok" if db_health.get("ok") else "failed"}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="is-mono is-muted">{esc(db_health.get("path"))} · '
            f'{esc(fmt_bytes(db_health.get("size_bytes") or 0))}</div>',
            unsafe_allow_html=True,
        )
        if st.button("Run integrity check again", key="health_integrity"):
            ok = database.integrity_check()
            if ok:
                st.success("SQLite integrity check passed.")
            else:
                st.error("SQLite integrity check failed. Back up the database immediately.")

    with right:
        section("Watched directories")
        for name, info in dirs.items():
            state = "ok" if info.get("writable") else "bad"
            st.markdown(
                f'<div class="is-health">{_dot(state)} {esc(name.title())}: '
                f'<span class="is-mono">{esc(info.get("path"))}</span></div>',
                unsafe_allow_html=True,
            )
            if not info.get("exists"):
                st.caption("Directory missing; restart the daemon or create it.")
            elif not info.get("writable"):
                st.caption("Directory is not writable by this user.")
        disk = health.get("disk", {})
        if disk.get("free_gb") is not None:
            st.caption(
                f"Disk free: {disk['free_gb']} GB of {disk['total_gb']} GB "
                "(visible to the dashboard process)."
            )

        section("Dashboard refresh")
        current = int(database.get_setting("dashboard_refresh_seconds") or settings.dashboard_refresh_seconds)
        options = [3, 5, 10, 30, 60]
        if current not in options:
            options.append(current)
            options.sort()
        choice = st.selectbox(
            "Auto-refresh interval (seconds)",
            options=options,
            index=options.index(current),
            key="settings_refresh",
        )
        if st.button("Save refresh interval", key="settings_save_refresh"):
            database.set_setting("dashboard_refresh_seconds", str(choice))
            st.success("Refresh interval saved. The dashboard will reload.")
            st.rerun()
        st.caption("Set to 0 in the environment to disable auto-refresh at startup.")

        section("Configured settings")
        table = [
            ("Model", settings.model),
            ("Watched folder", str(settings.incoming_dir)),
            ("Database", str(settings.db_path)),
            ("Max file size", f"{settings.max_file_size_mb} MB"),
            ("Supported files", ", ".join(settings.supported_extensions)),
            ("PDF pages inspected", str(settings.max_pdf_pages)),
            ("Duplicate date window", f"{settings.duplicate_date_window_days} days"),
            ("Vendor similarity threshold", f"{settings.vendor_similarity_threshold:.0%}"),
            ("Inference timeout", f"{settings.ollama_timeout_seconds:.0f}s"),
            ("Daemon workers", str(settings.worker_count)),
            ("Local currencies", ", ".join(settings.supported_currencies[:8]) + ("..." if len(settings.supported_currencies) > 8 else "")),
        ]
        for key, value in table:
            st.markdown(
                f'<div class="is-kv"><span>{esc(key)}</span><span>{esc(value)}</span></div>',
                unsafe_allow_html=True,
            )

    section("Privacy boundary")
    st.markdown(
        "Invoices and extracted financial data stay on this machine. Inference is "
        "performed only by the local Ollama service configured above; there is no "
        "cloud LLM fallback and no telemetry in this application. The dashboard is "
        "intended to bind to localhost. Remote exposure of an unauthenticated "
        "financial dashboard is not supported."
    )

    section("Known limitations")
    st.markdown(
        "- Extraction accuracy depends on the local model and document quality; "
        "every field should be reviewed before payment.\n"
        "- Duplicate detection produces investigation signals, not proof of fraud.\n"
        "- The dashboard reads a shared SQLite database; it does not itself run inference.\n"
        "- IMAP ingestion is not enabled by default and is not part of the verified demo.\n"
        "- Amounts are stored in hundredths; currencies with different minor-unit "
        "conventions are normalized to two decimals for comparison."
    )
    st.markdown(
        f'<div class="is-muted" style="margin-top:1rem">InboxShield AI {esc(__version__)} · '
        f'last dashboard refresh {esc(refreshed_at)} UTC</div>',
        unsafe_allow_html=True,
    )
    if not ai.get("available"):
        empty_state(
            "The dashboard is usable without Ollama, but new documents cannot be processed.",
            "Existing ledger data remains available and auditable.",
        )
