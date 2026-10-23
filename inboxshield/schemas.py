"""Pydantic schemas for model output and normalization/validation rules."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from inboxshield.config import Settings
from inboxshield.models import ExtractionOutcome
from inboxshield.utils.money import format_minor, parse_amount_optional
from inboxshield.utils.text import normalize_vendor, shorten

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_PLACEHOLDERS = {
    "",
    "-",
    "--",
    "n/a",
    "na",
    "none",
    "null",
    "not provided",
    "not available",
    "unknown",
    "(not provided)",
    "(none)",
}
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
)

EXTRACTION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "vendor": {"type": ["string", "null"]},
        "invoice_number": {"type": ["string", "null"]},
        "total_amount": {"type": ["string", "null"]},
        "currency": {"type": ["string", "null"]},
        "invoice_date": {"type": ["string", "null"]},
        "due_date": {"type": ["string", "null"]},
        "subtotal": {"type": ["string", "null"]},
        "tax_amount": {"type": ["string", "null"]},
        "missing_fields": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "vendor",
        "invoice_number",
        "total_amount",
        "currency",
        "invoice_date",
        "due_date",
        "subtotal",
        "tax_amount",
        "missing_fields",
    ],
}


def _stringify(value: Any) -> Any:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    return value


class RawExtraction(BaseModel):
    """Raw JSON shape requested from the model (all fields optional/nullable)."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    vendor: str | None = None
    invoice_number: str | None = None
    total_amount: str | None = None
    currency: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    subtotal: str | None = None
    tax_amount: str | None = None
    missing_fields: list[str] = Field(default_factory=list)

    @field_validator(
        "vendor",
        "invoice_number",
        "total_amount",
        "currency",
        "invoice_date",
        "due_date",
        "subtotal",
        "tax_amount",
        mode="before",
    )
    @classmethod
    def coerce_scalars(cls, value: Any) -> Any:
        return _stringify(value)

    @field_validator("missing_fields", mode="before")
    @classmethod
    def coerce_missing_fields(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []


def normalize_date(
    value: str | None,
    *,
    today: date | None = None,
) -> tuple[str | None, str | None]:
    """Return (ISO date or None, issue or None). Ambiguous dates are rejected."""
    if value is None:
        return None, None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a", "unknown"}:
        return None, None

    text = text.split("T")[0].strip() if "T" in text and len(text) > 10 else text
    parsed: date | None = None

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            parsed = date.fromisoformat(text)
        except ValueError:
            return None, f"Invalid date: {shorten(value, 40)}"
    elif re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", text):
        first, second, _year = (int(part) for part in text.split("/"))
        if first > 12 and second <= 12:
            fmt = "%d/%m/%Y"
        elif second > 12 and first <= 12:
            fmt = "%m/%d/%Y"
        else:
            return None, (
                f"Ambiguous numeric date {text!r}: day/month order is unclear; "
                "not normalized"
            )
        try:
            parsed = datetime.strptime(text, fmt).date()  # noqa: DTZ007
        except ValueError:
            return None, f"Invalid date: {shorten(value, 40)}"
    else:
        for fmt in _DATE_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt).date()  # noqa: DTZ007
                break
            except ValueError:
                continue
        if parsed is None:
            return None, f"Invalid or unrecognized date: {shorten(value, 40)}"

    reference = today or datetime.now(UTC).date()
    if parsed < date(1990, 1, 1):
        return None, f"Implausible date (before 1990): {parsed.isoformat()}"
    if parsed > reference + timedelta(days=730):
        return None, f"Implausible future date: {parsed.isoformat()}"
    return parsed.isoformat(), None


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return None if text.casefold() in _PLACEHOLDERS else text


def _normalize_currency(
    value: str | None, supported: tuple[str, ...]
) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    code = str(value).strip().upper()
    if not code or code in {"NULL", "NONE", "N/A", "UNKNOWN"}:
        return None, None
    if not _CURRENCY_RE.fullmatch(code):
        return None, (
            f"Currency {shorten(value, 30)!r} is not a 3-letter ISO code; "
            "not guessed from location"
        )
    if code not in supported:
        return code, (
            f"Currency {code} is outside the configured supported-currency "
            "policy; review required"
        )
    return code, None


def _check_consistency(
    amount_minor: int | None,
    subtotal_minor: int | None,
    tax_minor: int | None,
) -> list[str]:
    issues: list[str] = []
    if (
        subtotal_minor is not None
        and amount_minor is not None
        and subtotal_minor > amount_minor
    ):
        issues.append("Subtotal is greater than the total amount")
    if tax_minor is not None and amount_minor is not None and tax_minor > amount_minor:
        issues.append("Tax amount is greater than the total amount")
    if (
        subtotal_minor is not None
        and tax_minor is not None
        and amount_minor is not None
    ):
        expected = subtotal_minor + tax_minor
        if abs(expected - amount_minor) > 2:
            issues.append(
                "Subtotal plus tax does not equal the extracted total "
                f"({format_minor(subtotal_minor)} + {format_minor(tax_minor)} "
                f"!= {format_minor(amount_minor)}); values were not altered"
            )
    return issues


def normalize_extraction(
    raw: RawExtraction | dict[str, Any],
    settings: Settings,
    *,
    pages_inspected: int = 1,
) -> ExtractionOutcome:
    """Validate and normalize a single model response into the domain outcome."""
    try:
        parsed = raw if isinstance(raw, RawExtraction) else RawExtraction.model_validate(raw)
    except Exception as exc:  # pydantic validation error, malformed shape
        return ExtractionOutcome(
            success=False,
            error=shorten(f"Model output did not match the extraction schema: {exc}", 300),
            pages_inspected=pages_inspected,
        )

    issues: list[str] = []
    missing: set[str] = {field for field in parsed.missing_fields if field}

    vendor_original = (parsed.vendor or "").strip() or None
    vendor = normalize_vendor(vendor_original) or None
    if vendor_original is None:
        missing.add("vendor")
        issues.append("Vendor is missing")
    elif len(vendor_original) > 200:
        vendor_original = vendor_original[:200]
        issues.append("Vendor name was truncated to 200 characters")
    elif not vendor:
        vendor_original = None
        missing.add("vendor")
        issues.append("Vendor name is empty after normalization")

    invoice_number = _clean_optional_text(parsed.invoice_number)
    if invoice_number is None:
        missing.add("invoice_number")
        issues.append("Invoice number is missing")
    elif len(invoice_number) > 80:
        invoice_number = invoice_number[:80]
        issues.append("Invoice number was truncated to 80 characters")

    _amount_decimal, amount_minor, amount_issue = parse_amount_optional(parsed.total_amount)
    if amount_issue:
        issues.append(f"Total amount rejected: {amount_issue}")
    if amount_minor is None:
        missing.add("total_amount")
        issues.append("Total amount is missing or invalid")

    currency, currency_issue = _normalize_currency(parsed.currency, settings.supported_currencies)
    if currency_issue:
        issues.append(currency_issue)
    if currency is None:
        missing.add("currency")
        issues.append("Currency is missing or invalid")

    invoice_date, date_issue = normalize_date(parsed.invoice_date)
    if date_issue:
        issues.append(f"Invoice date: {date_issue}")
    if invoice_date is None:
        missing.add("invoice_date")

    due_date, due_issue = normalize_date(parsed.due_date)
    if due_issue:
        issues.append(f"Due date: {due_issue}")
    if invoice_date and due_date and due_date < invoice_date:
        issues.append("Due date is earlier than the invoice date")

    _, subtotal_minor, subtotal_issue = parse_amount_optional(
        parsed.subtotal, allow_zero=True
    )
    if subtotal_issue:
        issues.append(f"Subtotal rejected: {subtotal_issue}")
    _, tax_minor, tax_issue = parse_amount_optional(
        parsed.tax_amount, allow_zero=True
    )
    if tax_issue:
        issues.append(f"Tax amount rejected: {tax_issue}")

    issues.extend(_check_consistency(amount_minor, subtotal_minor, tax_minor))

    return ExtractionOutcome(
        success=True,
        vendor=vendor,
        vendor_original=vendor_original,
        invoice_number=invoice_number,
        amount=format_minor(amount_minor),
        amount_minor=amount_minor,
        currency=currency,
        invoice_date=invoice_date,
        due_date=due_date,
        subtotal=format_minor(subtotal_minor),
        subtotal_minor=subtotal_minor,
        tax_amount=format_minor(tax_minor),
        tax_minor=tax_minor,
        pages_inspected=pages_inspected,
        missing_fields=sorted(missing),
        issues=issues,
        raw_pages=[parsed.model_dump()],
    )


def merge_page_extractions(
    page_outcomes: list[ExtractionOutcome],
) -> ExtractionOutcome:
    """Merge per-page extraction results without inventing or correcting values."""
    usable = [outcome for outcome in page_outcomes if outcome.success]
    if not usable:
        errors = [outcome.error for outcome in page_outcomes if outcome.error]
        return ExtractionOutcome(
            success=False,
            error=shorten(
                "No page could be extracted. " + "; ".join(errors)
                if errors
                else "No page could be extracted",
                400,
            ),
            pages_inspected=len(page_outcomes),
        )

    def score(outcome: ExtractionOutcome) -> int:
        return sum(
            [
                bool(outcome.vendor),
                outcome.amount_minor is not None,
                bool(outcome.currency),
                bool(outcome.invoice_number),
                bool(outcome.invoice_date),
                bool(outcome.vendor_original),
            ]
        )

    primary = max(usable, key=score)
    merged = ExtractionOutcome(**{**primary.__dict__})
    merged.pages_inspected = len(page_outcomes)
    merged.raw_pages = [page for outcome in usable for page in outcome.raw_pages]
    merged.issues = list(dict.fromkeys(primary.issues))
    for outcome in usable:
        for issue in outcome.issues:
            if issue not in merged.issues:
                merged.issues.append(issue)

    if merged.vendor_original is None:
        for candidate in usable:
            if candidate.vendor_original:
                merged.vendor = candidate.vendor
                merged.vendor_original = candidate.vendor_original
                break
    if merged.amount_minor is None:
        for candidate in usable:
            if candidate.amount_minor is not None:
                merged.amount = candidate.amount
                merged.amount_minor = candidate.amount_minor
                break
    if merged.currency is None:
        for candidate in usable:
            if candidate.currency:
                merged.currency = candidate.currency
                break
    if merged.invoice_number is None:
        for candidate in usable:
            if candidate.invoice_number:
                merged.invoice_number = candidate.invoice_number
                break
    if merged.invoice_date is None:
        for candidate in usable:
            if candidate.invoice_date:
                merged.invoice_date = candidate.invoice_date
                break
    if merged.due_date is None:
        for candidate in usable:
            if candidate.due_date:
                merged.due_date = candidate.due_date
                break
    if merged.subtotal_minor is None:
        for candidate in usable:
            if candidate.subtotal_minor is not None:
                merged.subtotal = candidate.subtotal
                merged.subtotal_minor = candidate.subtotal_minor
                break
    if merged.tax_minor is None:
        for candidate in usable:
            if candidate.tax_minor is not None:
                merged.tax_amount = candidate.tax_amount
                merged.tax_minor = candidate.tax_minor
                break

    totals = {
        outcome.amount_minor
        for outcome in usable
        if outcome.amount_minor is not None
    }
    if len(totals) > 1 and merged.amount_minor is not None:
        merged.issues.append(
            "Conflicting total amounts were extracted from different pages; "
            "review required"
        )
    invoice_numbers = {
        outcome.invoice_number for outcome in usable if outcome.invoice_number
    }
    if len(invoice_numbers) > 1:
        merged.issues.append(
            "Conflicting invoice numbers were extracted from different pages; "
            "review required"
        )

    still_missing = {
        "vendor" if not merged.vendor else None,
        "total_amount" if merged.amount_minor is None else None,
        "currency" if not merged.currency else None,
        "invoice_date" if not merged.invoice_date else None,
    }
    resolved_issues = {
        "Vendor is missing": bool(merged.vendor),
        "Total amount is missing or invalid": merged.amount_minor is not None,
        "Currency is missing or invalid": bool(merged.currency),
    }
    merged.issues = [
        issue
        for issue in merged.issues
        if not resolved_issues.get(issue, False)
    ]
    merged.missing_fields = sorted(
        {field for field in still_missing if field}
        | {
            field
            for field in merged.missing_fields
            if field
            and not (
                (field in {"vendor", "vendor_original"} and merged.vendor)
                or (field == "total_amount" and merged.amount_minor is not None)
                or (field == "currency" and merged.currency)
                or (field == "invoice_date" and merged.invoice_date)
            )
        }
    )
    merged.success = True
    merged.error = None
    return merged
