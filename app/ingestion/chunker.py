import re

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from app.ingestion.config import IngestionSettings

_PAGE_MARKER_RE = re.compile(r"<!-- page:(\d+) -->\n?")
_HEADERS_TO_SPLIT_ON = [("#", "h1"), ("##", "h2"), ("###", "h3")]


def _mark_pages(pages: list[dict]) -> str:
    # The marker is appended AFTER each page's text (not prepended). MarkdownHeaderTextSplitter
    # groups any non-header line into the section that is currently open when it encounters that
    # line. A marker placed before a page's text sits right before the next header line and gets
    # swallowed by the *previous* section instead of the one it's meant to identify. Placing the
    # marker at the end of each page's own text means it lands inside the section that actually
    # contains that page's content (and correctly bleeds into a continuing section when a page
    # break occurs mid-section, since there's no header boundary to stop it there).
    return "\n\n".join(f"{page['text']}\n<!-- page:{page['page_number']} -->" for page in pages)


def _page_range(text: str) -> tuple[int, int] | None:
    page_numbers = [int(match) for match in _PAGE_MARKER_RE.findall(text)]
    if not page_numbers:
        return None
    return min(page_numbers), max(page_numbers)


def chunk_markdown(pages: list[dict], settings: IngestionSettings) -> list[dict]:
    marked_document = _mark_pages(pages)

    header_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=_HEADERS_TO_SPLIT_ON)
    header_sections = header_splitter.split_text(marked_document)

    char_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )

    # Sort by header level explicitly (h1, h2, h3) rather than relying on dict insertion
    # order, which is an implementation detail of MarkdownHeaderTextSplitter, not a contract.
    level_order = {key: index for index, (_, key) in enumerate(_HEADERS_TO_SPLIT_ON)}

    chunks: list[dict] = []
    for section in header_sections:
        section_path = [
            section.metadata[key] for key in sorted(section.metadata, key=lambda key: level_order[key])
        ]
        section_range = _page_range(section.page_content) or (1, 1)

        for piece in char_splitter.split_text(section.page_content):
            clean_text = _PAGE_MARKER_RE.sub("", piece).strip()
            if not clean_text:
                continue

            piece_range = _page_range(piece) or section_range
            chunks.append(
                {
                    "text": clean_text,
                    "section_path": section_path,
                    "page_start": piece_range[0],
                    "page_end": piece_range[1],
                    "char_count": len(clean_text),
                }
            )

    return chunks
