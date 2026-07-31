from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from app.ingestion.config import IngestionSettings

_HEADERS_TO_SPLIT_ON = [("#", "h1"), ("##", "h2"), ("###", "h3")]
# Sort section_path by header level explicitly (h1, h2, h3) rather than relying on dict
# insertion order, which is an implementation detail of MarkdownHeaderTextSplitter, not a
# documented contract.
_HEADER_LEVEL_ORDER = {key: index for index, (_, key) in enumerate(_HEADERS_TO_SPLIT_ON)}


def _build_document(pages: list[dict]) -> tuple[str, list[tuple[int, int, int]]]:
    """Concatenate page texts (no inline markers) into one document and return it
    alongside a parallel list of (start_offset, end_offset, page_number) spans, one
    per page, describing where each page's text lives in the concatenated document.
    """
    parts: list[str] = []
    spans: list[tuple[int, int, int]] = []
    offset = 0
    for index, page in enumerate(pages):
        text = page["text"]
        start = offset
        parts.append(text)
        offset += len(text)
        spans.append((start, offset, page["page_number"]))
        if index != len(pages) - 1:
            parts.append("\n\n")
            offset += 2
    return "".join(parts), spans


def _pages_overlapping(start: int, end: int, spans: list[tuple[int, int, int]]) -> tuple[int, int] | None:
    """Return the (min, max) page numbers whose span overlaps [start, end)."""
    page_numbers = [
        page_number for span_start, span_end, page_number in spans if span_start < end and span_end > start
    ]
    if not page_numbers:
        return None
    return min(page_numbers), max(page_numbers)


def _locate(needle: str, haystack: str, search_from: int) -> int:
    """Find `needle` in `haystack` at or after `search_from`.

    Text splitters (MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter) slice
    verbatim substrings of their input, so an exact match should always be found. A
    forward-advancing cursor keeps the search anchored to document order even when
    RecursiveCharacterTextSplitter produces overlapping pieces (the cursor only needs
    to advance past the *start* of the previous match, not its end, to keep finding
    correctly ordered results while still allowing backward overlap).
    """
    idx = haystack.find(needle, search_from)
    if idx != -1:
        return idx
    # Should not happen given splitters preserve substrings verbatim, but fall back to
    # an unanchored search rather than silently mis-locating content.
    return haystack.find(needle)


def chunk_markdown(pages: list[dict], settings: IngestionSettings) -> list[dict]:
    full_text, page_spans = _build_document(pages)
    if not full_text.strip():
        return []

    header_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=_HEADERS_TO_SPLIT_ON)
    header_sections = header_splitter.split_text(full_text)

    char_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )

    fallback_page = pages[0]["page_number"]
    chunks: list[dict] = []
    section_cursor = 0
    for section in header_sections:
        section_path = [
            section.metadata[key] for key in sorted(section.metadata, key=lambda key: _HEADER_LEVEL_ORDER[key])
        ]

        section_start = _locate(section.page_content, full_text, section_cursor)
        section_cursor = section_start + len(section.page_content)

        piece_cursor = 0
        for piece in char_splitter.split_text(section.page_content):
            clean_text = piece.strip()
            if not clean_text:
                continue

            piece_start_in_section = _locate(piece, section.page_content, piece_cursor)
            piece_cursor = piece_start_in_section + 1  # allow the next overlapping piece to be found
            piece_start = section_start + piece_start_in_section
            piece_end = piece_start + len(piece)

            page_range = _pages_overlapping(piece_start, piece_end, page_spans) or (fallback_page, fallback_page)
            chunks.append(
                {
                    "text": clean_text,
                    "section_path": section_path,
                    "page_start": page_range[0],
                    "page_end": page_range[1],
                    "char_count": len(clean_text),
                }
            )

    return chunks
