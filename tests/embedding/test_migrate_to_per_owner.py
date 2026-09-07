import uuid

import pytest

from app.auth.repository import create_user
from app.core.db import get_session_factory
from app.embedding.config import EmbeddingSettings
from app.embedding.index import FaissIndex, OwnerFaissIndexStore
from app.embedding.migrate_to_per_owner import migrate
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


def test_migrate_redistributes_legacy_shared_index_into_per_owner_indexes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.embedding.migrate_to_per_owner.get_embedding_settings",
        lambda: EmbeddingSettings(dimension=4),
    )

    legacy_index_path = str(tmp_path / "legacy" / "faiss_index.bin")
    legacy_index = FaissIndex(legacy_index_path, dimension=4)

    session_factory = get_session_factory()
    with session_factory() as session:
        owner_a = create_user(session, f"migrate-a-{uuid.uuid4()}@test", "x")
        owner_b = create_user(session, f"migrate-b-{uuid.uuid4()}@test", "x")
        session.commit()
        owner_a_id, owner_b_id = owner_a.id, owner_b.id

        document_a, document_b = f"doc-a-{uuid.uuid4()}", f"doc-b-{uuid.uuid4()}"
        records_a = save_document_and_chunks(
            session, document_a, "a.pdf", [_chunk(document_a, 0), _chunk(document_a, 1)], owner_a_id
        )
        records_b = save_document_and_chunks(
            session, document_b, "b.pdf", [_chunk(document_b, 0)], owner_b_id
        )
        session.commit()
        vector_ids_a = [r.vector_id for r in records_a]
        vector_ids_b = [r.vector_id for r in records_b]

    vectors_a = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
    vectors_b = [[0.0, 0.0, 1.0, 0.0]]
    legacy_index.add(vector_ids_a, vectors_a)
    legacy_index.add(vector_ids_b, vectors_b)
    legacy_index.save()

    index_dir = str(tmp_path / "per-owner")
    summary = migrate(legacy_index_path, index_dir)

    assert summary[str(owner_a_id)] == 2
    assert summary[str(owner_b_id)] == 1

    store = OwnerFaissIndexStore(index_dir, dimension=4)
    hits_a = store.search(owner_a_id, [1.0, 0.0, 0.0, 0.0], k=10)
    hits_b = store.search(owner_b_id, [0.0, 0.0, 1.0, 0.0], k=10)
    assert {vid for vid, _ in hits_a} == set(vector_ids_a)
    assert {vid for vid, _ in hits_b} == set(vector_ids_b)

    # Owner A's index never contains owner B's vector, and vice versa.
    assert not (set(vector_ids_b) & {vid for vid, _ in hits_a})
    assert not (set(vector_ids_a) & {vid for vid, _ in hits_b})

    # Reconstructed vectors are byte-for-byte what was originally added.
    reconstructed = FaissIndex(str(tmp_path / "per-owner" / f"{owner_a_id}.bin"), dimension=4)
    assert reconstructed.reconstruct(vector_ids_a[0]) == pytest.approx(vectors_a[0])
    assert reconstructed.reconstruct(vector_ids_a[1]) == pytest.approx(vectors_a[1])


def test_migrate_never_modifies_the_legacy_index(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.embedding.migrate_to_per_owner.get_embedding_settings",
        lambda: EmbeddingSettings(dimension=4),
    )

    legacy_index_path = str(tmp_path / "legacy.bin")
    legacy_index = FaissIndex(legacy_index_path, dimension=4)

    session_factory = get_session_factory()
    with session_factory() as session:
        owner = create_user(session, f"migrate-noop-{uuid.uuid4()}@test", "x")
        session.commit()
        owner_id = owner.id
        document_id = f"doc-{uuid.uuid4()}"
        records = save_document_and_chunks(
            session, document_id, "a.pdf", [_chunk(document_id, 0)], owner_id
        )
        session.commit()
        vector_id = records[0].vector_id

    legacy_index.add([vector_id], [[1.0, 0.0, 0.0, 0.0]])
    legacy_index.save()

    migrate(legacy_index_path, str(tmp_path / "per-owner"))

    reloaded_legacy = FaissIndex(legacy_index_path, dimension=4)
    assert reloaded_legacy.ntotal == 1


def test_migrate_is_safe_to_rerun_and_rebuilds_the_same_per_owner_result(tmp_path, monkeypatch):
    """Re-running after a partial/failed run must not corrupt or duplicate a rebuild.

    `migrate` always rebuilds full per-owner indexes from Postgres + the legacy index rather
    than incrementally patching, so running it twice against the same inputs must produce
    the same per-owner vector counts, not doubled ones.
    """
    monkeypatch.setattr(
        "app.embedding.migrate_to_per_owner.get_embedding_settings",
        lambda: EmbeddingSettings(dimension=4),
    )

    legacy_index_path = str(tmp_path / "legacy-rerun.bin")
    legacy_index = FaissIndex(legacy_index_path, dimension=4)

    session_factory = get_session_factory()
    with session_factory() as session:
        owner = create_user(session, f"migrate-rerun-{uuid.uuid4()}@test", "x")
        session.commit()
        owner_id = owner.id
        document_id = f"doc-{uuid.uuid4()}"
        records = save_document_and_chunks(
            session, document_id, "a.pdf", [_chunk(document_id, 0)], owner_id
        )
        session.commit()
        vector_id = records[0].vector_id

    legacy_index.add([vector_id], [[1.0, 0.0, 0.0, 0.0]])
    legacy_index.save()

    index_dir = str(tmp_path / "per-owner-rerun")
    first_summary = migrate(legacy_index_path, index_dir)
    second_summary = migrate(legacy_index_path, index_dir)

    assert first_summary[str(owner_id)] == 1
    assert second_summary[str(owner_id)] == 1

    store = OwnerFaissIndexStore(index_dir, dimension=4)
    hits = store.search(owner_id, [1.0, 0.0, 0.0, 0.0], k=10)
    assert len(hits) == 1  # not doubled by re-running
