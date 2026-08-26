from app.ingestion.models import Base, ChunkRecord, DocumentRecord


def test_document_record_table_name():
    assert DocumentRecord.__tablename__ == "documents"


def test_chunk_record_table_name():
    assert ChunkRecord.__tablename__ == "chunks"


def test_chunk_record_columns_present():
    columns = {c.name for c in ChunkRecord.__table__.columns}
    assert columns == {
        "chunk_id",
        "document_id",
        "chunk_index",
        "text",
        "section_path",
        "page_start",
        "page_end",
        "char_count",
        "parser_used",
        "source_filename",
        "vector_id",
        "search_vector",
    }


def test_base_metadata_knows_both_tables():
    assert {"documents", "chunks"} <= set(Base.metadata.tables)
