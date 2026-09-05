import uuid

from app.core.db import get_session_factory
from app.ingestion.repository import (
    get_chunks_by_vector_ids,
    get_sibling_chunks,
    save_document_and_chunks,
    search_chunks_by_text,
)
from app.ingestion.schemas import Chunk

_TEST_OWNER_ID = uuid.uuid4()


def _ensure_test_owner(session):
    from app.auth.models import UserRecord

    if session.get(UserRecord, _TEST_OWNER_ID) is None:
        session.add(UserRecord(id=_TEST_OWNER_ID, email=f"{_TEST_OWNER_ID}@test", hashed_password="x"))
        session.flush()


def _chunk(
    document_id: str, index: int, text: str | None = None, section_path: list[str] | None = None
) -> Chunk:
    return Chunk(
        chunk_id=f"{document_id}-{index}",
        document_id=document_id,
        chunk_index=index,
        text=text if text is not None else f"chunk text {index}",
        section_path=section_path if section_path is not None else ["Intro"],
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
        _ensure_test_owner(session)
        records = save_document_and_chunks(session, document_id, "doc.pdf", chunks, _TEST_OWNER_ID)
        session.commit()

        assert len(records) == 2
        assert all(isinstance(record.vector_id, int) for record in records)
        assert records[0].vector_id != records[1].vector_id
        assert [r.chunk_id for r in records] == ["doc-repo-test-0", "doc-repo-test-1"]


def test_get_chunks_by_vector_ids_returns_rows_keyed_by_vector_id():
    document_id = "doc-lookup-test"
    chunks = [_chunk(document_id, 0), _chunk(document_id, 1)]

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        records = save_document_and_chunks(session, document_id, "doc.pdf", chunks, _TEST_OWNER_ID)
        session.commit()
        vector_ids = [record.vector_id for record in records]

    with session_factory() as session:
        found = get_chunks_by_vector_ids(session, vector_ids, _TEST_OWNER_ID)

        assert set(found.keys()) == set(vector_ids)
        for vector_id, record in found.items():
            assert record.vector_id == vector_id
            assert record.document_id == document_id


def test_get_chunks_by_vector_ids_empty_input_returns_empty_dict():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert get_chunks_by_vector_ids(session, [], _TEST_OWNER_ID) == {}


def test_get_chunks_by_vector_ids_ignores_unknown_ids():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert get_chunks_by_vector_ids(session, [999_999_999], _TEST_OWNER_ID) == {}


def test_search_chunks_by_text_ranks_matching_chunk_first():
    document_id = "doc-fts-test"
    chunks = [
        _chunk(document_id, 0, text="giraffes are tall herbivorous mammals from Africa"),
        _chunk(document_id, 1, text="the stock market closed lower on Tuesday"),
    ]

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        records = save_document_and_chunks(session, document_id, "doc.pdf", chunks, _TEST_OWNER_ID)
        session.commit()
        vector_ids = [record.vector_id for record in records]

    with session_factory() as session:
        results = search_chunks_by_text(session, "giraffes Africa", k=5, owner_id=_TEST_OWNER_ID)

    assert results
    assert results[0][0] == vector_ids[0]


def test_search_chunks_by_text_no_match_returns_empty_list():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert search_chunks_by_text(session, "zzzznonexistentqueryterm", k=5, owner_id=_TEST_OWNER_ID) == []


def test_search_chunks_by_text_empty_query_returns_empty_list():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert search_chunks_by_text(session, "", k=5, owner_id=_TEST_OWNER_ID) == []


def test_get_sibling_chunks_returns_only_matching_section_ordered_by_chunk_index():
    document_id = "doc-siblings-test"
    chunks = [
        _chunk(document_id, 0, section_path=["Chapter 1", "Background"]),
        _chunk(document_id, 1, section_path=["Chapter 1", "Background"]),
        _chunk(document_id, 2, section_path=["Chapter 1", "Methods"]),
    ]

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        save_document_and_chunks(session, document_id, "doc.pdf", chunks, _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        siblings = get_sibling_chunks(session, document_id, ["Chapter 1", "Background"])

    assert [chunk.chunk_id for chunk in siblings] == [f"{document_id}-0", f"{document_id}-1"]


def test_get_sibling_chunks_honors_exclude_chunk_ids():
    document_id = "doc-siblings-exclude-test"
    chunks = [
        _chunk(document_id, 0, section_path=["Intro"]),
        _chunk(document_id, 1, section_path=["Intro"]),
    ]

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        save_document_and_chunks(session, document_id, "doc.pdf", chunks, _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        siblings = get_sibling_chunks(
            session, document_id, ["Intro"], exclude_chunk_ids={f"{document_id}-0"}
        )

    assert [chunk.chunk_id for chunk in siblings] == [f"{document_id}-1"]


def test_get_sibling_chunks_no_matching_section_returns_empty_list():
    document_id = "doc-siblings-empty-test"
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        save_document_and_chunks(session, document_id, "doc.pdf", [_chunk(document_id, 0)], _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        assert get_sibling_chunks(session, document_id, ["Nonexistent Section"]) == []
