from app.core.db import get_session_factory
from app.ingestion.repository import save_document_and_chunks
from app.ingestion.schemas import Chunk


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


def test_save_document_and_chunks_persists_rows_and_assigns_vector_ids():
    document_id = "doc-repo-test"
    chunks = [_chunk(document_id, 0), _chunk(document_id, 1)]

    session_factory = get_session_factory()
    with session_factory() as session:
        records = save_document_and_chunks(session, document_id, "doc.pdf", chunks)
        session.commit()

        assert len(records) == 2
        assert all(isinstance(record.vector_id, int) for record in records)
        assert records[0].vector_id != records[1].vector_id
        assert [r.chunk_id for r in records] == ["doc-repo-test-0", "doc-repo-test-1"]
