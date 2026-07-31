from app.ingestion.chunker import chunk_markdown
from app.ingestion.config import IngestionSettings


def _settings(**overrides):
    return IngestionSettings(**{"chunk_size": 1500, "chunk_overlap": 200, "ocr_text_threshold": 20, **overrides})


def test_splits_on_headers_and_tracks_page_range():
    pages = [
        {"text": "# Title\nIntro text.\n## Section One\nBody of section one.", "page_number": 1},
        {"text": "## Section Two\nBody of section two.", "page_number": 2},
    ]
    chunks = chunk_markdown(pages, _settings())

    assert len(chunks) >= 2
    section_one = next(c for c in chunks if "section one" in c["text"].lower())
    assert section_one["page_start"] == 1
    assert section_one["page_end"] == 1
    assert section_one["section_path"] == ["Title", "Section One"]

    section_two = next(c for c in chunks if "section two" in c["text"].lower())
    assert section_two["page_start"] == 2
    assert section_two["page_end"] == 2


def test_no_page_marker_leaks_into_chunk_text():
    pages = [{"text": "# Title\nSome body text here.", "page_number": 1}]
    chunks = chunk_markdown(pages, _settings())
    for chunk in chunks:
        assert "page:" not in chunk["text"]
        assert "<!--" not in chunk["text"]


def test_large_section_is_split_by_char_limit_with_overlap():
    long_body = "word " * 800  # ~4000 chars, well over a small chunk_size
    pages = [{"text": f"# Title\n{long_body}", "page_number": 1}]
    chunks = chunk_markdown(pages, _settings(chunk_size=500, chunk_overlap=50))

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk["char_count"] <= 500 + 50  # allow overlap slack
        assert chunk["char_count"] == len(chunk["text"])


def test_empty_page_produces_no_chunks():
    pages = [{"text": "", "page_number": 1}]
    chunks = chunk_markdown(pages, _settings())
    assert chunks == []


def test_page_numbers_not_starting_at_one_with_multiple_headers_on_one_page():
    # Regression test: a single page containing more than one header (h1 title followed
    # by an h2 subsection) used to get split into multiple sections by
    # MarkdownHeaderTextSplitter, but a marker-based page-tracking scheme could only ever
    # attach the page number to the LAST of those sections — earlier sections on the same
    # page silently fell back to page 1 regardless of the document's actual page numbers.
    # Using page numbers that don't start at 1 (5 and 6) makes that wrong fallback visible:
    # a bug that defaults to "page 1" is indistinguishable from a correct answer when the
    # real first page happens to be 1.
    pages = [
        {"text": "# Title\nIntro text.\n## Section One\nBody of section one.", "page_number": 5},
        {"text": "## Section Two\nBody of section two.", "page_number": 6},
    ]
    chunks = chunk_markdown(pages, _settings())

    intro = next(c for c in chunks if "intro text" in c["text"].lower())
    assert intro["page_start"] == 5
    assert intro["page_end"] == 5

    section_one = next(c for c in chunks if "section one" in c["text"].lower())
    assert section_one["page_start"] == 5
    assert section_one["page_end"] == 5

    section_two = next(c for c in chunks if "section two" in c["text"].lower())
    assert section_two["page_start"] == 6
    assert section_two["page_end"] == 6
