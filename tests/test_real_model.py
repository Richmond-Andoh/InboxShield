"""Opt-in end-to-end test using the real local Gemma model through Ollama.

Run with:
    INBOXSHIELD_REAL_MODEL_TEST=1 .venv/bin/python -m pytest -m real_model -s
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from inboxshield.ai.extractor import GemmaExtractor
from inboxshield.ai.ollama_client import OllamaClient
from inboxshield.config import get_settings

pytestmark = pytest.mark.real_model

if not os.environ.get("INBOXSHIELD_REAL_MODEL_TEST"):
    pytest.skip(
        "Set INBOXSHIELD_REAL_MODEL_TEST=1 to run real local model inference",
        allow_module_level=True,
    )


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _make_invoice(path: Path) -> None:
    image = Image.new("RGB", (1100, 1500), "white")
    draw = ImageDraw.Draw(image)
    draw.text((70, 60), "HARBOUR VIEW HARDWARE LTD", fill="black", font=_font(44))
    draw.text((70, 125), "Tema, Ghana", fill="black", font=_font(24))
    draw.text((70, 220), "INVOICE", fill="black", font=_font(46))
    draw.text((70, 320), "Invoice Number: HV-2026-7781", fill="black", font=_font(28))
    draw.text((70, 370), "Invoice Date: 2026-10-20", fill="black", font=_font(28))
    draw.text((70, 420), "Due Date: 2026-11-19", fill="black", font=_font(28))
    draw.text((70, 500), "Bill To: Adenta Logistics Ltd", fill="black", font=_font(28))
    draw.line((70, 570, 1030, 570), fill="black", width=2)
    draw.text((70, 600), "Description", fill="black", font=_font(28))
    draw.text((760, 600), "Amount (GHS)", fill="black", font=_font(28))
    draw.text((70, 660), "Industrial drill bits, 5 sets", fill="black", font=_font(28))
    draw.text((760, 660), "1,600.00", fill="black", font=_font(28))
    draw.text((70, 720), "Safety goggles, 10 units", fill="black", font=_font(28))
    draw.text((760, 720), "400.00", fill="black", font=_font(28))
    draw.text((650, 830), "Subtotal", fill="black", font=_font(28))
    draw.text((880, 830), "2,000.00", fill="black", font=_font(28))
    draw.text((650, 890), "VAT (15%)", fill="black", font=_font(28))
    draw.text((880, 890), "300.00", fill="black", font=_font(28))
    draw.text((600, 970), "TOTAL DUE", fill="black", font=_font(38))
    draw.text((830, 965), "GHS 2,300.00", fill="black", font=_font(38))
    draw.text((70, 1400), "SYNTHETIC DEMO DOCUMENT - NOT A REAL INVOICE", fill="black")
    image.save(path)


def test_real_gemma_vision_extraction(tmp_path: Path) -> None:
    settings = get_settings()
    client = OllamaClient(settings)
    status = client.model_status()
    assert status["available"], status.get("error")
    assert status["model_present"], f"Model {settings.model} is not installed"
    assert "vision" in status["capabilities"], status["capabilities"]

    path = tmp_path / "real_invoice.png"
    _make_invoice(path)
    outcome = GemmaExtractor(settings, client).extract(path)
    assert outcome.success is True, outcome.error
    assert outcome.vendor_original is not None
    assert "HARBOUR" in outcome.vendor_original.upper()
    assert outcome.invoice_number == "HV-2026-7781"
    assert outcome.currency == "GHS"
    assert outcome.amount_minor == 230000
    assert outcome.invoice_date == "2026-10-20"
    assert outcome.pages_inspected == 1
