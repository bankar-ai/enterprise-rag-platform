from app.core.db import get_session_factory
from app.embedding.config import EmbeddingSettings
from app.embedding.index import FaissIndex
from app.ingestion.repository import save_document_and_chunks
from app.ingestion.schemas import Chunk
from app.retrieval.service import search


class _FakeEmbeddingClient:
    def __init__(self, vector):
        self._vector = vector
        self.calls = []

    def embed(self, texts):
        self.calls.append(texts)
        return [self._vector for _ in texts]


def _chunk(document_id: str, index: int) -> Chunk:
    return Chunk(
        chunk_id=f"{document_id}-{index}",
        document_id=document_id,
        chunk_index=index,
        text=f"chunk text {index}",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        char_count=13,
        parser_used="fast",
        source_filename="doc.pdf",
    )


def _persist_and_index(document_id, chunks, vectors, faiss_index):
    session_factory = get_session_factory()
    with session_factory() as session:
        records = save_document_and_chunks(session, document_id, "doc.pdf", chunks)
        session.commit()
        vector_ids = [record.vector_id for record in records]
    faiss_index.add(vector_ids, vectors)
    faiss_index.save()
    return vector_ids


def test_search_returns_ranked_chunks(tmp_path):
    document_id = "doc-search-test"
    chunks = [_chunk(document_id, 0), _chunk(document_id, 1)]
    vectors = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
    faiss_index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    _persist_and_index(document_id, chunks, vectors, faiss_index)

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    results = search(
        query="find chunk 0",
        top_k=5,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index=faiss_index,
    )

    assert fake_client.calls == [["find chunk 0"]]
    assert len(results) == 2
    assert results[0].chunk_id == "doc-search-test-0"
    assert results[0].score > results[1].score


def test_search_on_empty_index_returns_empty_list(tmp_path):
    faiss_index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])

    results = search(
        query="anything",
        top_k=5,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index=faiss_index,
    )

    assert results == []


def test_search_top_k_larger_than_available_returns_all(tmp_path):
    document_id = "doc-search-test-2"
    chunks = [_chunk(document_id, 0)]
    vectors = [[1.0, 0.0, 0.0, 0.0]]
    faiss_index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    _persist_and_index(document_id, chunks, vectors, faiss_index)

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    results = search(
        query="find it",
        top_k=10,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index=faiss_index,
    )

    assert len(results) == 1
