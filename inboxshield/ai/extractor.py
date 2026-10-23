"""Local vision extraction for invoice images and PDF pages."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Protocol

from PIL import Image, UnidentifiedImageError

from inboxshield.ai.ollama_client import (
    OllamaClient,
    OllamaError,
    OllamaModelMissingError,
    OllamaUnavailableError,
)
from inboxshield.config import Settings
from inboxshield.models import ExtractionOutcome
from inboxshield.schemas import (
    RawExtraction,
    merge_page_extractions,
    normalize_extraction,
)
from inboxshield.utils.logging_config import sanitize_error

try:  # PyMuPDF >= 1.24 exposes the ``pymupdf`` name; older builds use ``fitz``.
    import pymupdf  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - fallback for older PyMuPDF
    import fitz as pymupdf  # type: ignore[import-untyped,no-redef]

logger = logging.getLogger(__name__)

MIN_IMAGE_PIXELS = 40 * 40
MAX_IMAGE_PIXELS = 60_000_000


class DocumentError(RuntimeError):
    pass


class DocumentExtractor(Protocol):
    def extract(self, path: Path) -> ExtractionOutcome: ...


def _encode_image(image: Image.Image, settings: Settings) -> bytes:
    if image.mode != "RGB":
        image = image.convert("RGB")
    width, height = image.size
    if width < 40 or height < 40:
        raise DocumentError("Image is too small to be a readable invoice")
    largest = max(width, height)
    if largest > settings.max_image_dimension:
        scale = settings.max_image_dimension / largest
        image = image.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.LANCZOS,
        )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88, optimize=True)
    return buffer.getvalue()


def load_image(path: Path, settings: Settings) -> bytes:
    try:
        with Image.open(path) as image:
            image.load()
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise DocumentError(
                    f"Image resolution {image.width}x{image.height} exceeds the "
                    "safe processing limit"
                )
            return _encode_image(image, settings)
    except UnidentifiedImageError as exc:
        raise DocumentError("File is not a readable image (corrupt or unsupported)") from exc
    except OSError as exc:
        raise DocumentError(f"Could not read image: {sanitize_error(exc, 150)}") from exc


def render_pdf_pages(path: Path, settings: Settings) -> list[bytes]:
    """Render up to ``max_pdf_pages`` pages, resource-consciously, to JPEG."""
    try:
        document = pymupdf.open(str(path))
    except Exception as exc:
        raise DocumentError(f"Could not open PDF: {sanitize_error(exc, 150)}") from exc
    try:
        if document.is_encrypted and not document.authenticate(""):
            raise DocumentError("PDF is password-protected and cannot be read")
        page_count = document.page_count
        if page_count < 1:
            raise DocumentError("PDF has no pages")
        pages: list[bytes] = []
        for index in range(min(page_count, settings.max_pdf_pages)):
            page = document.load_page(index)
            matrix = pymupdf.Matrix(
                settings.pdf_render_dpi / 72.0, settings.pdf_render_dpi / 72.0
            )
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            try:
                pages.append(_encode_image(image, settings))
            finally:
                image.close()
        return pages
    finally:
        document.close()


def document_pages(path: Path, settings: Settings) -> list[bytes]:
    extension = path.suffix.lower()
    if extension == ".pdf":
        return render_pdf_pages(path, settings)
    if extension in {".png", ".jpg", ".jpeg"}:
        return [load_image(path, settings)]
    raise DocumentError(f"Unsupported file extension: {extension or '(none)'}")


class GemmaExtractor:
    """Extracts invoice data using the configured local Gemma model."""

    def __init__(self, settings: Settings, client: OllamaClient | None = None) -> None:
        self.settings = settings
        self.client = client or OllamaClient(settings)

    def extract(self, path: Path) -> ExtractionOutcome:
        try:
            pages = document_pages(path, self.settings)
        except DocumentError as exc:
            return ExtractionOutcome(success=False, error=str(exc))
        if not pages:
            return ExtractionOutcome(success=False, error="Document has no readable pages")

        outcomes: list[ExtractionOutcome] = []
        fatal_error: str | None = None
        retryable = False
        for index, page_bytes in enumerate(pages, start=1):
            try:
                raw = self.client.extract_json(page_bytes, index, len(pages))
                outcome = normalize_extraction(
                    RawExtraction.model_validate(raw),
                    self.settings,
                    pages_inspected=index,
                )
            except OllamaUnavailableError as exc:
                fatal_error = str(exc)
                retryable = True
                break
            except OllamaModelMissingError as exc:
                fatal_error = str(exc)
                retryable = False
                break
            except OllamaError as exc:
                retryable = retryable or exc.retryable
                outcomes.append(
                    ExtractionOutcome(
                        success=False,
                        error=f"Page {index}: {sanitize_error(exc, 250)}",
                        pages_inspected=index,
                    )
                )
                continue
            except Exception as exc:  # defensive: malformed model output
                outcomes.append(
                    ExtractionOutcome(
                        success=False,
                        error=f"Page {index}: {sanitize_error(exc, 250)}",
                        pages_inspected=index,
                    )
                )
                continue

            outcomes.append(outcome)

        if fatal_error is not None:
            return ExtractionOutcome(
                success=False,
                error=sanitize_error(fatal_error, 400),
                pages_inspected=len(outcomes),
                retryable=retryable,
            )

        merged = merge_page_extractions(outcomes)
        if not merged.success:
            suffix = " (transient failure; may be retried)" if retryable else ""
            merged.error = (merged.error or "Extraction failed") + suffix
            merged.retryable = retryable
        return merged

    def health(self) -> dict[str, object]:
        return self.client.model_status()
