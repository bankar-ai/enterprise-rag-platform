from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from app.ingestion.config import IngestionSettings

_HEADERS_TO_SPLIT_ON = [("#", "h1"), ("##", "h2"), ("###", "h3")]
# Sort section_path by header level explicitly (h1, h2, h3) rather than relying on dict
# insertion order, which is an implementation detail of MarkdownHeaderTextSplitter, not a
# documented contract.
_HEADER_LEVEL_ORDER = {key: index for index, (_, key) in enumerate(_HEADERS_TO_SPLIT_ON)}


def _build_document(pages: list[dict]) -> str:
    """Concatenate page texts (no inline markers) into one document for the header splitter."""
    return "\n\n".join(page["text"] for page in pages)


def _normalize_line(line: str) -> str:
    """Apply the exact transform MarkdownHeaderTextSplitter applies to each line before
    using it as page_content: `.strip()`, then drop non-printable characters. Applying the
    same transform to our reference line index lets us match lines by equality instead of
    by verbatim substring search.
    """
    return "".join(filter(str.isprintable, line.strip()))


def _build_line_index(pages: list[dict]) -> list[tuple[str, int]]:
    """Build an ordered, flat list of (normalized_line_text, page_number) for every
    non-blank line of every page, in document order. This is the reference MarkdownHeaderTextSplitter's
    reconstructed section.page_content lines are matched against, since page_content is no
    longer a verbatim substring of the original document once a section spans more than one
    line (aggregate_lines_to_chunks joins lines with "  \\n" and split_text strips every line).
    """
    line_index: list[tuple[str, int]] = []
    for page in pages:
        for raw_line in page["text"].split("\n"):
            normalized = _normalize_line(raw_line)
            if normalized:
                line_index.append((normalized, page["page_number"]))
    return line_index


def _locate_line(line: str, line_index: list[tuple[str, int]], search_from: int) -> int:
    """Find `line` in `line_index` at or after `search_from`, by equality (not substring
    search) since `line_index` entries are already normalized whole lines.

    Fails loudly rather than silently defaulting a page number: MarkdownHeaderTextSplitter
    never merges, reorders, or invents lines, so every normalized line coming out of a
    section should exist in `line_index` at or after the cursor. If it doesn't, page
    tracking can no longer be trusted, and guessing (e.g. falling back to page 1) would
    reintroduce the exact silent-misattribution bug class earlier review rounds eliminated.
    """
    for index in range(search_from, len(line_index)):
        if line_index[index][0] == line:
            return index
    raise ValueError(
        "chunker: could not locate expected line in the page line index at or after the "
        "expected position; the header splitter may have produced a line that doesn't "
        "trace back to any input page, so page tracking can no longer be trusted"
    )


def _section_page_range(
    section_content: str, line_index: list[tuple[str, int]], cursor: int
) -> tuple[tuple[int, int], int]:
    """Determine the (page_start, page_end) of a header section by matching its
    constituent lines (split back out of the reconstructed page_content, then normalized
    the same way as `line_index`) against the reference line index with a forward-advancing
    cursor. Returns the page range plus the cursor position to resume from for the next
    section, preserving document order while tolerating duplicate lines elsewhere in the
    document.
    """
    section_lines = [_normalize_line(line) for line in section_content.split("\n")]
    section_lines = [line for line in section_lines if line]
    if not section_lines:
        raise ValueError("chunker: header section has no content lines to locate")

    pages_found: list[int] = []
    for line in section_lines:
        index = _locate_line(line, line_index, cursor)
        pages_found.append(line_index[index][1])
        cursor = index + 1

    return (min(pages_found), max(pages_found)), cursor


def chunk_markdown(pages: list[dict], settings: IngestionSettings) -> list[dict]:
    full_text = _build_document(pages)
    if not full_text.strip():
        return []

    line_index = _build_line_index(pages)

    header_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=_HEADERS_TO_SPLIT_ON)
    header_sections = header_splitter.split_text(full_text)

    char_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )

    chunks: list[dict] = []
    line_cursor = 0
    for section in header_sections:
        section_path = [
            section.metadata[key] for key in sorted(section.metadata, key=lambda key: _HEADER_LEVEL_ORDER[key])
        ]

        page_range, line_cursor = _section_page_range(section.page_content, line_index, line_cursor)

        for piece in char_splitter.split_text(section.page_content):
            # MarkdownHeaderTextSplitter joins separate paragraphs within a section with
            # "  \n" (aggregate_lines_to_chunks) rather than the original document's blank
            # line; normalize that internal joiner back to a plain newline so it doesn't
            # leak into chunk text as a splitter implementation artifact.
            clean_text = piece.replace("  \n", "\n").strip()
            if not clean_text:
                continue

            # Character-level pieces within a section can fragment mid-line, so they can't
            # be cleanly mapped back to individual lines/pages the way whole sections can.
            # Falling back to the section-level page range for every piece within it is an
            # already-accepted tradeoff from an earlier review round.
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
