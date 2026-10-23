"""Reusable dashboard components."""

from __future__ import annotations

import html
import io
from pathlib import Path
from typing import Any

import streamlit as st
from PIL import Image

from inboxshield.config import Settings
from inboxshield.database import Database
from inboxshield.ingestion.file_utils import is_within

try:
    import pymupdf
except ImportError:  # pragma: no cover
    import fitz as pymupdf  # type: ignore[no-redef]

STATUS_LABELS = {
    "VERIFIED_LOGGED": "Verified / logged",
    "FLAGGED_DUPLICATE": "Flagged duplicate",
    "FLAGGED_REVIEW": "Review required",
    "FAILED": "Failed",
    "PROCESSING": "Processing",
    "QUEUED": "Queued",
    "COMPLETED": "Completed",
    "SKIPPED_DUPLICATE": "Exact duplicate",
    "PENDING": "Pending review",
    "REVIEWED": "Reviewed",
    "RUNNING": "Running",
    "STOPPED": "Stopped",
}

STATUS_TONES = {
    "VERIFIED_LOGGED": "verified",
    "COMPLETED": "verified",
    "FLAGGED_DUPLICATE": "duplicate",
    "SKIPPED_DUPLICATE": "duplicate",
    "FLAGGED_REVIEW": "review",
    "PENDING": "review",
    "FAILED": "failed",
    "PROCESSING": "processing",
    "QUEUED": "processing",
    "REVIEWED": "verified",
    "RUNNING": "verified",
    "STOPPED": "failed",
}


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def format_money(minor: int | None, currency: str | None = None) -> str:
    if minor is None:
        return "-"
    formatted = f"{minor / 100:,.2f}"
    return f"{currency} {formatted}" if currency else formatted


def fmt_bytes(value: int | float | None) -> str:
    if not value:
        return "0 B"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def status_badge(status: str | None) -> str:
    if not status:
        return '<span class="is-badge neutral">Unknown</span>'
    label = STATUS_LABELS.get(status, status.replace("_", " ").title())
    tone = STATUS_TONES.get(status, "neutral")
    return f'<span class="is-badge {tone}">{esc(label)}</span>'


def page_header(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="is-page-title">{esc(title)}</div>'
        f'<div class="is-page-sub">{esc(subtitle)}</div>',
        unsafe_allow_html=True,
    )


def metric_card(
    label: str, value: str, hint: str = "", tone: str = "teal"
) -> str:
    tone_class = "" if tone == "teal" else tone
    return (
        f'<div class="is-card is-metric-tone {tone_class}">'
        f'<div class="is-metric-label">{esc(label)}</div>'
        f'<div class="is-metric-value">{esc(value)}</div>'
        f'<div class="is-metric-hint">{esc(hint)}</div>'
        "</div>"
    )


def metric_row(cards: list[tuple[str, str, str, str]]) -> None:
    columns = st.columns(len(cards))
    for column, (label, value, hint, tone) in zip(columns, cards, strict=False):
        with column:
            st.markdown(
                metric_card(label, value, hint, tone), unsafe_allow_html=True
            )


def section(title: str, subtitle: str = "") -> None:
    suffix = f' <span class="is-muted">{esc(subtitle)}</span>' if subtitle else ""
    st.markdown(
        f'<div class="is-section">{esc(title)}{suffix}</div>', unsafe_allow_html=True
    )


def empty_state(message: str, hint: str = "") -> None:
    extra = f'<div class="is-muted" style="margin-top:.4rem">{esc(hint)}</div>' if hint else ""
    st.markdown(
        f'<div class="is-empty">{esc(message)}{extra}</div>', unsafe_allow_html=True
    )


def kv_list(items: list[tuple[str, Any]]) -> None:
    rows = "".join(
        f'<div class="is-kv"><span>{esc(key)}</span><span>{esc(value)}</span></div>'
        for key, value in items
    )
    st.markdown(rows, unsafe_allow_html=True)


def finding_card(finding: dict[str, Any]) -> None:
    risk = str(finding.get("risk_level", "LOW")).lower()
    review = finding.get("review_status", "PENDING")
    note = finding.get("reviewer_note")
    matched = finding.get("matched_invoice_id")
    meta_parts = [
        f"Finding #{finding.get('id')}",
        f"{finding.get('finding_type', '').replace('_', ' ').title()}",
        f"Risk: {finding.get('risk_level')}",
        f"{STATUS_LABELS.get(review, review)}",
    ]
    if matched:
        meta_parts.append(f"Matched invoice #{matched}")
    if note:
        meta_parts.append(f"Note: {note}")
    st.markdown(
        f'<div class="is-finding {esc(risk)}">'
        f'<div class="is-finding-title">{esc(finding.get("finding_type", "").replace("_", " ").title())}</div>'
        f'<div class="is-finding-body">{esc(finding.get("explanation", ""))}</div>'
        f'<div class="is-finding-meta">{esc(" · ".join(meta_parts))}</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def review_form(
    database: Database,
    finding: dict[str, Any],
    *,
    key_prefix: str,
) -> None:
    if finding.get("review_status") != "PENDING":
        return
    finding_id = int(finding["id"])
    with st.form(key=f"{key_prefix}-review-{finding_id}", clear_on_submit=True):
        note = st.text_input(
            "Reviewer note",
            key=f"{key_prefix}-note-{finding_id}",
            placeholder="What did you verify?",
            max_chars=500,
        )
        submitted = st.form_submit_button("Mark finding reviewed", type="primary")
        if submitted:
            if database.review_finding(finding_id, note):
                st.success(f"Finding #{finding_id} marked reviewed.")
                st.rerun()
            else:
                st.error("Finding could not be updated.")


def render_findings(
    database: Database,
    findings: list[dict[str, Any]],
    *,
    key_prefix: str,
    allow_review: bool = True,
) -> None:
    if not findings:
        st.markdown('<div class="is-muted">No audit findings recorded.</div>', unsafe_allow_html=True)
        return
    for finding in findings:
        finding_card(finding)
        if allow_review and finding.get("review_status") == "PENDING":
            review_form(database, finding, key_prefix=key_prefix)


def _allowed_preview_path(file_path: str | None, settings: Settings) -> Path | None:
    if not file_path:
        return None
    try:
        candidate = Path(file_path).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if candidate.is_symlink() or not candidate.is_file():
        return None
    if not (
        is_within(candidate, settings.processed_dir)
        or is_within(candidate, settings.incoming_dir)
    ):
        return None
    if candidate.stat().st_size > settings.max_file_size_bytes:
        return None
    return candidate


def preview_bytes(file_path: str | None, settings: Settings) -> bytes | None:
    """Return safe image bytes only for locally archived/ingested documents."""
    path = _allowed_preview_path(file_path, settings)
    if path is None:
        return None
    try:
        if path.suffix.lower() == ".pdf":
            document = pymupdf.open(str(path))
            try:
                if document.is_encrypted and not document.authenticate(""):
                    return None
                page = document.load_page(0)
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.4, 1.4), alpha=False)
                image = Image.frombytes(
                    "RGB", (pixmap.width, pixmap.height), pixmap.samples
                )
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=85)
                return buffer.getvalue()
            finally:
                document.close()
        with Image.open(path) as image:
            image.load()
            if image.mode != "RGB":
                image = image.convert("RGB")
            largest = max(image.size)
            if largest > 1600:
                scale = 1600 / largest
                image = image.resize(
                    (int(image.width * scale), int(image.height * scale)),
                    Image.Resampling.LANCZOS,
                )
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=85)
            return buffer.getvalue()
    except Exception:
        return None


def invoice_option_label(invoice: dict[str, Any]) -> str:
    vendor = invoice.get("vendor_original") or invoice.get("vendor") or "Unknown vendor"
    amount = format_money(invoice.get("amount_minor"), invoice.get("currency"))
    return f"#{invoice.get('id')} · {vendor} · {amount}"


def dataframe_rows(rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    return [{column: row.get(column) for column in columns} for row in rows]
