#!/usr/bin/env python3
"""Generate synthetic, fictional invoices for demos and integration tests.

These are real image/PDF files (not pre-computed AI results). Put them through
the normal watcher pipeline to demonstrate Gemma extraction and duplicate flags.
No real vendors, bank accounts, or personal data are used.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont

try:
    import pymupdf
except ImportError:  # pragma: no cover
    import fitz as pymupdf  # type: ignore[no-redef]

WIDTH, HEIGHT = 1240, 1650
INK = "#0f172a"
MUTED = "#475569"
ACCENT = "#0f766e"
LINE = "#cbd5e1"
DEMO_MARKER = "SYNTHETIC DEMO DOCUMENT - NOT A REAL INVOICE"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _new_page() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 14), fill=ACCENT)
    return image, draw


def _draw_header(
    draw: ImageDraw.ImageDraw,
    vendor: str,
    address: str,
    title: str = "INVOICE",
) -> int:
    draw.text((70, 70), vendor, fill=INK, font=_font(44, bold=True))
    draw.text((70, 128), address, fill=MUTED, font=_font(23))
    draw.text((WIDTH - 400, 80), title, fill=ACCENT, font=_font(52, bold=True))
    draw.line((70, 190, WIDTH - 70, 190), fill=LINE, width=2)
    return 240


def _draw_meta(
    draw: ImageDraw.ImageDraw,
    y: int,
    *,
    invoice_number: str | None,
    invoice_date: str | None,
    due_date: str | None,
    bill_to: str,
) -> int:
    label_font = _font(23, bold=True)
    value_font = _font(23)
    rows = [
        ("Invoice Number:", invoice_number or ""),
        ("Invoice Date:", invoice_date or ""),
        ("Due Date:", due_date or ""),
        ("Bill To:", bill_to),
    ]
    for label, value in rows:
        draw.text((70, y), label, fill=MUTED, font=label_font)
        draw.text((330, y), value, fill=INK, font=value_font)
        y += 42
    return y + 30


def _draw_items(
    draw: ImageDraw.ImageDraw,
    y: int,
    items: list[tuple[str, str]],
    currency: str | None,
) -> int:
    header_font = _font(23, bold=True)
    body_font = _font(23)
    draw.text((70, y), "Description", fill=MUTED, font=header_font)
    amount_header = f"Amount ({currency})" if currency else "Amount"
    draw.text((840, y), amount_header, fill=MUTED, font=header_font)
    y += 40
    draw.line((70, y, WIDTH - 70, y), fill=LINE, width=2)
    y += 18
    for description, amount in items:
        draw.text((70, y), description, fill=INK, font=body_font)
        draw.text((840, y), amount, fill=INK, font=body_font)
        y += 44
    return y


def _draw_totals(
    draw: ImageDraw.ImageDraw,
    y: int,
    *,
    subtotal: str,
    tax: str,
    total: str,
    currency: str | None,
    show_currency: bool = True,
) -> int:
    label_font = _font(24)
    value_font = _font(24)
    draw.line((620, y, WIDTH - 70, y), fill=LINE, width=2)
    y += 20
    draw.text((620, y), "Subtotal", fill=MUTED, font=label_font)
    draw.text((900, y), subtotal, fill=INK, font=value_font)
    y += 42
    draw.text((620, y), "VAT (15%)", fill=MUTED, font=label_font)
    draw.text((900, y), tax, fill=INK, font=value_font)
    y += 50
    draw.rectangle((600, y - 8, WIDTH - 60, y + 62), outline=ACCENT, width=2)
    draw.text((620, y + 6), "TOTAL DUE", fill=ACCENT, font=_font(30, bold=True))
    total_text = f"{currency} {total}" if show_currency and currency else total
    draw.text((880, y + 4), total_text, fill=INK, font=_font(30, bold=True))
    return y + 100


def _finish(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    path: Path,
    note: str | None = None,
) -> Path:
    y = HEIGHT - 150
    if note:
        draw.text((70, y - 40), note, fill=MUTED, font=_font(20))
    draw.line((70, y, WIDTH - 70, y), fill=LINE, width=1)
    draw.text((70, y + 14), DEMO_MARKER, fill="#b45309", font=_font(20, bold=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=True)
    image.close()
    return path


def render_invoice(
    path: Path,
    *,
    vendor: str,
    address: str,
    invoice_number: str | None,
    invoice_date: str | None,
    due_date: str | None,
    bill_to: str,
    items: list[tuple[str, str]],
    subtotal: str | None,
    tax: str | None,
    total: str | None,
    currency: str | None,
    title: str = "INVOICE",
    note: str | None = None,
    show_currency: bool = True,
    totals_on_page: bool = True,
) -> Path:
    image, draw = _new_page()
    y = _draw_header(draw, vendor, address, title)
    y = _draw_meta(
        draw,
        y,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        due_date=due_date,
        bill_to=bill_to,
    )
    y = _draw_items(draw, y, items, currency if show_currency else None)
    if totals_on_page and subtotal and tax and total:
        y = _draw_totals(
            draw,
            y + 20,
            subtotal=subtotal,
            tax=tax,
            total=total,
            currency=currency,
            show_currency=show_currency,
        )
    elif not totals_on_page:
        draw.text(
            (70, y + 40),
            "Line items continued. Totals are shown on the final page.",
            fill=MUTED,
            font=_font(22),
        )
    return _finish(image, draw, path, note)


def render_totals_page(
    path: Path,
    *,
    vendor: str,
    invoice_number: str,
    subtotal: str,
    tax: str,
    total: str,
    currency: str,
) -> Path:
    image, draw = _new_page()
    y = _draw_header(draw, vendor, "Final page - totals", "INVOICE (PAGE 2)")
    draw.text((70, y), f"Invoice Number: {invoice_number}", fill=INK, font=_font(26))
    draw.text((70, y + 45), "Continued line items and totals", fill=MUTED, font=_font(23))
    y = _draw_totals(
        draw,
        y + 120,
        subtotal=subtotal,
        tax=tax,
        total=total,
        currency=currency,
    )
    return _finish(image, draw, path)


def images_to_pdf(images: list[Path], output: Path) -> Path:
    document = pymupdf.open()
    try:
        for image_path in images:
            page = document.new_page(width=595, height=842)
            page.insert_image(
                pymupdf.Rect(0, 0, 595, 842),
                filename=str(image_path),
                keep_proportion=True,
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        document.save(str(output))
    finally:
        document.close()
    return output


def _clean(output: Path) -> int:
    removed = 0
    for pattern in ("DEMO-*",):
        for path in output.glob(pattern):
            if path.is_file() and path.name.startswith("DEMO-"):
                path.unlink()
                removed += 1
    return removed


def build_demo_set(output: Path, *, include_pdf: bool = True) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    normal = render_invoice(
        output / "DEMO-01_normal_accra_supplies.png",
        vendor="Accra Office Supplies Ltd",
        address="12 Independence Avenue, Accra, Ghana",
        invoice_number="INV-2026-0042",
        invoice_date="2026-10-20",
        due_date="2026-11-20",
        bill_to="Adenta Logistics Ltd",
        items=[
            ("A4 copy paper, 10 reams", "800.00"),
            ("Laser toner cartridge", "300.00"),
        ],
        subtotal="1,100.00",
        tax="150.00",
        total="1,250.00",
        currency="GHS",
    )
    created.append(normal)

    created.append(
        render_invoice(
            output / "DEMO-02_normal_kumasi_printworks.png",
            vendor="Kumasi Print Works",
            address="8 Harper Road, Kumasi, Ghana",
            invoice_number="KP-1187",
            invoice_date="2026-10-18",
            due_date="2026-11-17",
            bill_to="Adenta Logistics Ltd",
            items=[
                ("Branded letterheads, 500 sheets", "420.00"),
                ("Business cards, 200 units", "136.52"),
            ],
            subtotal="556.52",
            tax="83.48",
            total="640.00",
            currency="GHS",
        )
    )

    exact_copy = output / "DEMO-03_exact_copy_of_01.png"
    shutil.copyfile(normal, exact_copy)
    created.append(exact_copy)

    created.append(
        render_invoice(
            output / "DEMO-04_potential_duplicate_accra.png",
            vendor="Accra Office Supplies Ltd",
            address="12 Independence Avenue, Accra, Ghana",
            invoice_number="INV-2026-0043",
            invoice_date="2026-10-21",
            due_date="2026-11-21",
            bill_to="Adenta Logistics Ltd",
            items=[
                ("Office consumables restock", "1,250.00"),
            ],
            subtotal="1,087.00",
            tax="163.00",
            total="1,250.00",
            currency="GHS",
            note="Same vendor, currency, and total as INV-2026-0042 but a different invoice number.",
        )
    )

    created.append(
        render_invoice(
            output / "DEMO-05_number_conflict_accra.png",
            vendor="Accra Office Supplies Ltd",
            address="12 Independence Avenue, Accra, Ghana",
            invoice_number="INV-2026-0042",
            invoice_date="2026-10-22",
            due_date="2026-11-22",
            bill_to="Adenta Logistics Ltd",
            items=[
                ("Office furniture - two desks", "1,234.78"),
                ("Delivery", "185.22"),
            ],
            subtotal="1,420.00",
            tax="0.00",
            total="1,420.00",
            currency="GHS",
            note="Reuses invoice number INV-2026-0042 with a different total.",
        )
    )

    created.append(
        render_invoice(
            output / "DEMO-06_missing_fields.png",
            vendor="Tema Stationery Traders",
            address="Community 1, Tema, Ghana",
            invoice_number=None,
            invoice_date="2026-10-19",
            due_date=None,
            bill_to="Walk-in customer",
            items=[("Envelopes and folders", "1,100.00")],
            subtotal="900.00",
            tax="150.00",
            total="1,100.00",
            currency=None,
            title="RECEIPT",
            note="Invoice number and currency are intentionally absent; review required.",
            show_currency=False,
        )
    )

    if include_pdf:
        page_one = output / ".DEMO-07_page1.png"
        page_two = output / ".DEMO-07_page2.png"
        render_invoice(
            page_one,
            vendor="Volta Business Systems",
            address="3 Volta Road, Ho, Ghana",
            invoice_number="VBS-2026-0114",
            invoice_date="2026-10-15",
            due_date="2026-11-14",
            bill_to="Adenta Logistics Ltd",
            items=[
                ("Laptop docking stations, 4 units", "960.00"),
                ("USB-C cables, 8 units", "240.00"),
                ("On-site installation", "300.00"),
            ],
            subtotal=None,
            tax=None,
            total=None,
            currency="GHS",
            totals_on_page=False,
            note="Multi-page invoice: totals appear on page 2.",
        )
        render_totals_page(
            page_two,
            vendor="Volta Business Systems",
            invoice_number="VBS-2026-0114",
            subtotal="1,500.00",
            tax="225.00",
            total="1,725.00",
            currency="GHS",
        )
        pdf = images_to_pdf(
            [page_one, page_two], output / "DEMO-07_multipage_invoice.pdf"
        )
        created.append(pdf)
        page_one.unlink(missing_ok=True)
        page_two.unlink(missing_ok=True)

    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="incoming_invoices",
        help="Directory for generated files (default: incoming_invoices)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove existing DEMO-* files from the output directory first",
    )
    parser.add_argument(
        "--no-pdf",
        action="store_true",
        help="Skip the multi-page PDF scenario",
    )
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    if args.clean:
        removed = _clean(output)
        print(f"Removed {removed} previous demo file(s) from {output}")
    created = build_demo_set(output, include_pdf=not args.no_pdf)
    print(f"Generated {len(created)} synthetic invoice document(s) in {output}:")
    for path in created:
        print(f"  - {path.name}")
    print(
        "\nThese are real image/PDF files. The daemon must extract them with the "
        "local model; no database rows were created by this script."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
