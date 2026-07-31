from typing import Literal

import pymupdf4llm
from docling.document_converter import DocumentConverter

from app.ingestion.config import IngestionSettings

_PAGE_BREAK = "\n\n<!-- docling-page-break -->\n\n"


def parse_fast(pdf_path: str) -> list[dict]:
    """Raw PyMuPDF4LLM page_chunks output — used for both extraction and fallback routing."""
    return pymupdf4llm.to_markdown(pdf_path, page_chunks=True)


def needs_fallback(fast_pages: list[dict], ocr_text_threshold: int) -> bool:
    for page in fast_pages:
        if len(page["text"].strip()) < ocr_text_threshold:
            return True
        # Table signal has two possible shapes depending on pymupdf4llm mode:
        # a dedicated "tables" list (legacy/non-layout mode), or layout-mode's
        # "page_boxes" list of region dicts, some of which may have class "table".
        if len(page.get("tables") or []) > 0:
            return True
        if any(box.get("class") == "table" for box in (page.get("page_boxes") or [])):
            return True
    return False


def parse_quality(pdf_path: str) -> list[dict]:
    """Docling parse (quality path: better tables + OCR). Returns {"text", "page_number"} dicts."""
    converter = DocumentConverter()
    result = converter.convert(pdf_path)
    markdown = result.document.export_to_markdown(page_break_placeholder=_PAGE_BREAK)
    return [
        {"text": text, "page_number": index + 1}
        for index, text in enumerate(markdown.split(_PAGE_BREAK))
    ]


def parse_pdf(pdf_path: str, settings: IngestionSettings) -> tuple[list[dict], Literal["fast", "quality"]]:
    fast_pages = parse_fast(pdf_path)

    if needs_fallback(fast_pages, settings.ocr_text_threshold):
        return parse_quality(pdf_path), "quality"

    normalized = [
        {"text": page["text"], "page_number": page["metadata"]["page_number"]}
        for page in fast_pages
    ]
    return normalized, "fast"
