# tests/ingestion/test_jobs.py
from app.core.db import get_session_factory
from app.embedding.index import FaissIndex
from app.ingestion.config import IngestionSettings
from app.ingestion.jobs import create_job, get_job, run_ingestion_job
from app.ingestion.models import ChunkRecord
from app.ingestion.schemas import JobStatus


def _settings():
    return IngestionSettings(chunk_size=1500, chunk_overlap=200, ocr_text_threshold=20)


class _FakeEmbeddingClient:
    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]


def test_create_job_starts_pending():
    job_id = create_job()
    record = get_job(job_id)
    assert record.status == JobStatus.PENDING
    assert record.result is None
    assert record.error is None


def test_get_job_returns_none_for_unknown_id():
    assert get_job("does-not-exist") is None


def test_run_ingestion_job_marks_done_on_success(simple_text_pdf, tmp_path):
    job_id = create_job()
    run_ingestion_job(
        job_id,
        simple_text_pdf,
        "simple.pdf",
        _settings(),
        embedding_client=_FakeEmbeddingClient(),
        faiss_index=FaissIndex(str(tmp_path / "index.bin"), dimension=4),
    )

    record = get_job(job_id)
    assert record.status == JobStatus.DONE
    assert record.result is not None
    assert record.error is None


def test_run_ingestion_job_marks_failed_on_bad_path():
    job_id = create_job()
    run_ingestion_job(job_id, "/no/such/file.pdf", "missing.pdf", _settings())

    record = get_job(job_id)
    assert record.status == JobStatus.FAILED
    assert record.result is None
    assert record.error is not None


def test_run_ingestion_job_logs_on_failure(caplog):
    job_id = create_job()
    with caplog.at_level("ERROR"):
        run_ingestion_job(job_id, "/no/such/file.pdf", "missing.pdf", _settings())

    assert any(job_id in record.message for record in caplog.records)
    assert any(record.levelname == "ERROR" for record in caplog.records)


def test_run_ingestion_job_persists_chunks_and_vectors(simple_text_pdf, tmp_path):
    job_id = create_job()
    faiss_index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)

    run_ingestion_job(
        job_id,
        simple_text_pdf,
        "simple.pdf",
        _settings(),
        embedding_client=_FakeEmbeddingClient(),
        faiss_index=faiss_index,
    )

    record = get_job(job_id)
    assert record.status == JobStatus.DONE
    document_id = record.result.document_id

    assert faiss_index.ntotal == len(record.result.chunks)

    session_factory = get_session_factory()
    with session_factory() as session:
        rows = session.query(ChunkRecord).filter(ChunkRecord.document_id == document_id).all()
        assert len(rows) == len(record.result.chunks)


def test_run_ingestion_job_marks_failed_if_persistence_fails(simple_text_pdf, tmp_path):
    class _BrokenEmbeddingClient:
        def embed(self, texts):
            raise RuntimeError("embedding backend unreachable")

    job_id = create_job()
    run_ingestion_job(
        job_id,
        simple_text_pdf,
        "simple.pdf",
        _settings(),
        embedding_client=_BrokenEmbeddingClient(),
        faiss_index=FaissIndex(str(tmp_path / "index.bin"), dimension=4),
    )

    record = get_job(job_id)
    assert record.status == JobStatus.FAILED
    assert record.error is not None
    assert "embedding backend unreachable" in record.error
