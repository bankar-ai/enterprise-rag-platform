import pytest
from pydantic import ValidationError

from app.retrieval.schemas import RetrievalQuery, RetrievalResponse, RetrievedChunk


def test_retrieval_query_defaults_top_k_to_five():
    assert RetrievalQuery(query="what is x?").top_k == 5


def test_retrieval_query_defaults_rerank_to_false():
    assert RetrievalQuery(query="what is x?").rerank is False


def test_retrieval_query_defaults_expand_sections_to_false():
    assert RetrievalQuery(query="what is x?").expand_sections is False


def test_retrieval_query_rejects_empty_query():
    with pytest.raises(ValidationError):
        RetrievalQuery(query="")


def test_retrieval_query_rejects_top_k_out_of_bounds():
    with pytest.raises(ValidationError):
        RetrievalQuery(query="x", top_k=0)
    with pytest.raises(ValidationError):
        RetrievalQuery(query="x", top_k=51)


def test_retrieval_response_holds_ranked_chunks():
    chunk = RetrievedChunk(
        chunk_id="doc-1-0",
        document_id="doc-1",
        text="hello",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    response = RetrievalResponse(results=[chunk])
    assert response.results[0].score == 0.9
