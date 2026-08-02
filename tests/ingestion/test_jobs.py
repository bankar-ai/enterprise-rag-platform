# tests/ingestion/test_jobs.py
from app.ingestion.config import IngestionSettings
from app.ingestion.jobs import create_job, get_job, run_ingestion_job
from app.ingestion.schemas import JobStatus


def _settings():
    return IngestionSettings(chunk_size=1500, chunk_overlap=200, ocr_text_threshold=20)


def test_create_job_starts_pending():
    job_id = create_job()
    record = get_job(job_id)
    assert record.status == JobStatus.PENDING
    assert record.result is None
    assert record.error is None


def test_get_job_returns_none_for_unknown_id():
    assert get_job("does-not-exist") is None


def test_run_ingestion_job_marks_done_on_success(simple_text_pdf):
    job_id = create_job()
    run_ingestion_job(job_id, simple_text_pdf, "simple.pdf", _settings())

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
