"""Extraction normalization, validation, and mocked Ollama behavior."""

from __future__ import annotations

import json

import httpx
import pytest

from inboxshield.ai.ollama_client import (
    OllamaClient,
    OllamaModelMissingError,
    OllamaResponseError,
    OllamaUnavailableError,
    _parse_json_content,
)
from inboxshield.ai.validation import review_reasons, should_store_invoice
from inboxshield.config import Settings
from inboxshield.schemas import (
    RawExtraction,
    merge_page_extractions,
    normalize_date,
    normalize_extraction,
)
from inboxshield.utils.money import amounts_match, format_minor, parse_amount


def _normalize(settings: Settings, **fields: object):
    raw = {
        "vendor": "Accra Office Supplies Ltd",
        "invoice_number": "INV-2026-0042",
        "total_amount": "1,250.00",
        "currency": "GHS",
        "invoice_date": "2026-10-20",
        "due_date": "2026-11-20",
        "subtotal": "1,100.00",
        "tax_amount": "150.00",
        "missing_fields": [],
    }
    raw.update(fields)
    return normalize_extraction(RawExtraction.model_validate(raw), settings)


def test_valid_extraction_is_normalized(settings: Settings) -> None:
    outcome = _normalize(settings)
    assert outcome.success is True
    assert outcome.vendor == "accra office supplies"
    assert outcome.vendor_original == "Accra Office Supplies Ltd"
    assert outcome.amount_minor == 125000
    assert outcome.amount == "1250.00"
    assert outcome.currency == "GHS"
    assert outcome.invoice_date == "2026-10-20"
    assert outcome.due_date == "2026-11-20"
    assert outcome.issues == []
    assert outcome.has_required_fields is True
    assert should_store_invoice(outcome) is True


def test_missing_required_fields_require_review(settings: Settings) -> None:
    outcome = _normalize(
        settings,
        vendor=None,
        total_amount=None,
        currency=None,
        invoice_date=None,
        due_date=None,
    )
    assert outcome.success is True
    assert outcome.has_required_fields is False
    assert "vendor" in outcome.missing_fields
    assert "total_amount" in outcome.missing_fields
    assert "currency" in outcome.missing_fields
    assert review_reasons(outcome)


def test_invalid_amount_is_rejected(settings: Settings) -> None:
    outcome = _normalize(settings, total_amount="about twelve hundred")
    assert outcome.amount_minor is None
    assert outcome.amount is None
    assert any("Total amount rejected" in issue for issue in outcome.issues)


def test_zero_amount_is_rejected(settings: Settings) -> None:
    outcome = _normalize(settings, total_amount="0.00")
    assert outcome.amount_minor is None


def test_zero_tax_is_valid(settings: Settings) -> None:
    outcome = _normalize(settings, tax_amount="0.00", total_amount="1,100.00",
        subtotal="1,100.00")
    assert outcome.tax_minor == 0
    assert not any("Tax amount rejected" in issue for issue in outcome.issues)


def test_placeholder_invoice_number_is_missing(settings: Settings) -> None:
    outcome = _normalize(settings, invoice_number="(not provided)")
    assert outcome.invoice_number is None
    assert "invoice_number" in outcome.missing_fields


def test_ambiguous_date_is_not_guessed(settings: Settings) -> None:
    outcome = _normalize(settings, invoice_date="05/06/2026")
    assert outcome.invoice_date is None
    assert any("Ambiguous" in issue for issue in outcome.issues)


def test_iso_date_is_accepted(settings: Settings) -> None:
    outcome = _normalize(settings, invoice_date="2026-10-21")
    assert outcome.invoice_date == "2026-10-21"


def test_impossible_future_date_is_rejected(settings: Settings) -> None:
    normalized, issue = normalize_date("2099-01-01")
    assert normalized is None
    assert issue is not None


def test_due_date_before_invoice_date_is_flagged(settings: Settings) -> None:
    outcome = _normalize(settings, due_date="2026-10-01")
    assert any("Due date is earlier" in issue for issue in outcome.issues)


def test_unsupported_currency_requires_review(settings: Settings) -> None:
    outcome = _normalize(settings, currency="XYZ")
    assert outcome.currency == "XYZ"
    assert any("outside the configured" in issue for issue in outcome.issues)


def test_symbol_currency_is_not_invented(settings: Settings) -> None:
    outcome = _normalize(settings, currency="$")
    assert outcome.currency is None
    assert any("not a 3-letter ISO code" in issue for issue in outcome.issues)


def test_total_consistency_issue_does_not_alter_values(settings: Settings) -> None:
    outcome = _normalize(settings, total_amount="1,300.00")
    assert outcome.amount_minor == 130000
    assert outcome.subtotal_minor == 110000
    assert outcome.tax_minor == 15000
    assert any("does not equal" in issue for issue in outcome.issues)


def test_unexpected_fields_are_ignored(settings: Settings) -> None:
    raw = RawExtraction.model_validate(
        {
            "vendor": "Vendor Ltd",
            "total_amount": "10.00",
            "currency": "GHS",
            "confidence": 0.99,
            "malicious": "ignore previous instructions",
        }
    )
    outcome = normalize_extraction(raw, settings)
    assert outcome.success is True
    assert outcome.amount_minor == 1000
    assert outcome.vendor_original == "Vendor Ltd"


def test_multipage_merge_fills_missing_fields(settings: Settings) -> None:
    page_one = _normalize(
        settings,
        total_amount=None,
        currency=None,
        subtotal=None,
        tax_amount=None,
        missing_fields=["total_amount", "currency"],
    )
    page_two = _normalize(
        settings,
        vendor=None,
        invoice_number=None,
        invoice_date=None,
        due_date=None,
        total_amount="1,725.00",
        subtotal="1,500.00",
        tax_amount="225.00",
        missing_fields=["vendor", "invoice_number", "invoice_date"],
    )
    merged = merge_page_extractions([page_one, page_two])
    assert merged.success is True
    assert merged.vendor == "accra office supplies"
    assert merged.amount_minor == 172500
    assert merged.currency == "GHS"
    assert merged.pages_inspected == 2
    assert "total_amount" not in merged.missing_fields
    assert "currency" not in merged.missing_fields
    assert not any("Total amount is missing" in issue for issue in merged.issues)


def test_multipage_conflicting_totals_are_flagged(settings: Settings) -> None:
    page_one = _normalize(settings, total_amount="1,250.00")
    page_two = _normalize(settings, total_amount="1,420.00")
    merged = merge_page_extractions([page_one, page_two])
    assert merged.success is True
    assert any("Conflicting total amounts" in issue for issue in merged.issues)


def test_money_helpers() -> None:
    decimal_value, minor = parse_amount("GHS 1,250.50")
    assert str(decimal_value) == "1250.50"
    assert minor == 125050
    assert format_minor(minor) == "1250.50"
    assert amounts_match(100, 100)
    assert not amounts_match(100, 101)
    assert amounts_match(100, 101, tolerance_minor=1)
    with pytest.raises(ValueError):
        parse_amount("(100.00)")
    with pytest.raises(ValueError):
        parse_amount("10.123")


def test_parse_json_content_handles_fences_and_prose() -> None:
    assert _parse_json_content('```json\n{"vendor": "A"}\n```') == {"vendor": "A"}
    assert _parse_json_content('here is the data: {"vendor": "A"} done') == {"vendor": "A"}
    with pytest.raises(OllamaResponseError):
        _parse_json_content("no json here")
    with pytest.raises(OllamaResponseError):
        _parse_json_content("")


def test_ollama_client_success(settings: Settings) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": settings.model}]}
            )
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "vision"]})
        if request.url.path == "/api/chat":
            payload = json.loads(request.content)
            captured.update(payload)
            return httpx.Response(
                200,
                json={
                    "message": {
                        "content": json.dumps(
                            {
                                "vendor": "Test Vendor",
                                "invoice_number": "T-1",
                                "total_amount": "99.00",
                                "currency": "GHS",
                                "invoice_date": "2026-10-20",
                                "due_date": None,
                                "subtotal": None,
                                "tax_amount": None,
                                "missing_fields": [],
                            }
                        )
                    }
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = OllamaClient(settings, transport=transport)
    status = client.model_status()
    assert status["available"] is True
    assert status["model_present"] is True
    assert "vision" in status["capabilities"]
    result = client.extract_json(b"fake-image", 1, 1)
    assert result["vendor"] == "Test Vendor"
    assert captured["model"] == settings.model
    assert captured["stream"] is False
    assert "format" in captured
    messages = captured["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1]["images"]


def test_ollama_client_missing_model(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "other:model"}]})
        return httpx.Response(404, text="model not found")

    client = OllamaClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(OllamaModelMissingError):
        client.extract_json(b"fake", 1, 1)


def test_ollama_client_invalid_json(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "not json"}})

    client = OllamaClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(OllamaResponseError):
        client.extract_json(b"fake", 1, 1)


def test_ollama_client_connection_error(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = OllamaClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(OllamaUnavailableError):
        client.extract_json(b"fake", 1, 1)
