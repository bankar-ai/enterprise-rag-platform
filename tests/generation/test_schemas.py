import pytest
from pydantic import ValidationError

from app.generation.schemas import Citation, GenerationQuery, GenerationResponse


def test_generation_query_defaults():
    query = GenerationQuery(query="hello")
    assert query.top_k == 5
    assert query.rerank is False
    assert query.expand_sections is False


def test_generation_query_rejects_empty_query():
    with pytest.raises(ValidationError):
        GenerationQuery(query="")


def test_generation_query_rejects_top_k_out_of_bounds():
    with pytest.raises(ValidationError):
        GenerationQuery(query="hello", top_k=0)


def test_generation_response_round_trip():
    citation = Citation(
        chunk_id="c1",
        document_id="doc-1",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
    )
    response = GenerationResponse(answer="the answer [1]", citations=[citation])

    assert response.model_dump()["citations"][0]["chunk_id"] == "c1"
