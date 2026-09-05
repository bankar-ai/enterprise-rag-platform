import uuid

from app.core.db import get_session_factory
from app.embedding.config import EmbeddingSettings
from app.embedding.index import FaissIndex
from app.embedding.service import embed_and_persist
from app.ingestion.models import ChunkRecord
from app.ingestion.schemas import Chunk

_TEST_OWNER_ID = uuid.uuid4()


def _ensure_test_owner(session):
    from app.auth.models import UserRecord

    if session.get(UserRecord, _TEST_OWNER_ID) is None:
        session.add(UserRecord(id=_TEST_OWNER_ID, email=f"{_TEST_OWNER_ID}@test", hashed_password="x"))
        session.flush()


class _FakeEmbeddingClient:
    def __init__(self, dimension):
        self._dimension = dimension
        self.calls = []

    def embed(self, texts):
        self.calls.append(texts)
        return [[0.1] * self._dimension for _ in texts]


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


def test_embed_and_persist_writes_to_postgres_and_faiss(tmp_path):
    document_id = "doc-service-test"
    chunks = [_chunk(document_id, 0), _chunk(document_id, 1)]
    fake_client = _FakeEmbeddingClient(dimension=4)
    faiss_index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    settings = EmbeddingSettings(dimension=4)

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        session.commit()

    embed_and_persist(
        document_id=document_id,
        source_filename="doc.pdf",
        chunks=chunks,
        owner_id=_TEST_OWNER_ID,
        settings=settings,
        embedding_client=fake_client,
        faiss_index=faiss_index,
    )

    assert fake_client.calls == [["chunk text 0", "chunk text 1"]]
    assert faiss_index.ntotal == 2

    session_factory = get_session_factory()
    with session_factory() as session:
        rows = (
            session.query(ChunkRecord)
            .filter(ChunkRecord.document_id == document_id)
            .order_by(ChunkRecord.chunk_index)
            .all()
        )
        assert [row.chunk_id for row in rows] == ["doc-service-test-0", "doc-service-test-1"]


def test_embed_and_persist_noop_for_empty_chunks(tmp_path):
    faiss_index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    fake_client = _FakeEmbeddingClient(dimension=4)

    embed_and_persist(
        document_id="doc-empty",
        source_filename="doc.pdf",
        chunks=[],
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index=faiss_index,
    )

    assert fake_client.calls == []
    assert faiss_index.ntotal == 0
