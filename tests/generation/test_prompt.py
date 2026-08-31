from app.generation.prompt import build_prompt
from app.retrieval.schemas import RetrievedChunk


def _chunk(chunk_id, text, section_path=None):
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        text=text,
        section_path=section_path or ["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )


def test_build_prompt_numbers_chunks_and_appends_question():
    chunks = [_chunk("c1", "First chunk text."), _chunk("c2", "Second chunk text.")]

    user_prompt, included = build_prompt("What is X?", chunks, max_context_chars=1000)

    assert "[1] First chunk text." in user_prompt
    assert "[2] Second chunk text." in user_prompt
    assert "(source: doc.pdf, section: Intro)" in user_prompt
    assert user_prompt.endswith("Question: What is X?")
    assert included == chunks


def test_build_prompt_always_includes_first_chunk_even_if_it_alone_exceeds_budget():
    chunks = [_chunk("c1", "x" * 50)]

    user_prompt, included = build_prompt("q", chunks, max_context_chars=10)

    assert included == chunks
    assert "[1]" in user_prompt


def test_build_prompt_truncates_before_chunk_that_would_exceed_budget():
    chunks = [_chunk("c1", "a" * 20), _chunk("c2", "b" * 20), _chunk("c3", "c" * 20)]

    user_prompt, included = build_prompt("q", chunks, max_context_chars=25)

    assert included == chunks[:1]
    assert "[2]" not in user_prompt
    assert "b" * 20 not in user_prompt


def test_build_prompt_with_no_chunks_returns_empty_context():
    user_prompt, included = build_prompt("q", [], max_context_chars=1000)

    assert included == []
    assert user_prompt.endswith("Question: q")
