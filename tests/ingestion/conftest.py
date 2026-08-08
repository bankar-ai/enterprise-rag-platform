# tests/ingestion/conftest.py
import fitz
import pytest


@pytest.fixture
def multi_paragraph_pdf(tmp_path):
    """A one-page PDF with a heading and several lines of body text spanning multiple paragraphs.

    Includes a blank-line gap — realistic input that a single-line fixture can't exercise,
    since MarkdownHeaderTextSplitter reconstructs multi-line section content rather than
    preserving it verbatim.
    """
    path = tmp_path / "multi_paragraph.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Introduction", fontsize=18)
    page.insert_text((72, 100), "First paragraph of the intro, line one.", fontsize=11)
    page.insert_text((72, 118), "First paragraph of the intro, line two.", fontsize=11)
    page.insert_text((72, 150), "Second paragraph after a visual gap.", fontsize=11)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def table_pdf(tmp_path):
    """A one-page PDF with a drawn grid (table) — should trigger the quality fallback."""
    path = tmp_path / "table.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Data Table", fontsize=18)
    x0, y0, cell_w, cell_h = 72, 100, 100, 30
    for row in range(2):
        for col in range(2):
            rect = fitz.Rect(
                x0 + col * cell_w, y0 + row * cell_h, x0 + (col + 1) * cell_w, y0 + (row + 1) * cell_h
            )
            page.draw_rect(rect)
            page.insert_text((rect.x0 + 5, rect.y0 + 20), f"R{row}C{col}", fontsize=10)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def scanned_pdf(tmp_path):
    """A one-page PDF containing only an image, no extractable text — should trigger the OCR fallback."""
    path = tmp_path / "scanned.pdf"
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200))
    pix.set_rect(pix.irect, (255, 255, 255))
    page.insert_image(fitz.Rect(0, 0, page.rect.width, page.rect.height), pixmap=pix)
    doc.save(str(path))
    doc.close()
    return str(path)
