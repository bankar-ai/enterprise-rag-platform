"""In-memory async ingestion job tracking (no persistent queue; single-process only)."""

import threading
import uuid

from app.ingestion.config import IngestionSettings
from app.ingestion.schemas import IngestResponse, JobStatus
from app.ingestion.service import ingest_pdf

_jobs: dict[str, "JobRecord"] = {}
_lock = threading.Lock()


class JobRecord:
    """Mutable state for one tracked ingestion job."""

    def __init__(self) -> None:
        """Initialize a new job in PENDING status with no result or error yet."""
        self.status: JobStatus = JobStatus.PENDING
        self.result: IngestResponse | None = None
        self.error: str | None = None


def create_job() -> str:
    """Register a new PENDING job and return its ID."""
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = JobRecord()
    return job_id


def get_job(job_id: str) -> JobRecord | None:
    """Look up a job by ID, or None if it doesn't exist."""
    with _lock:
        return _jobs.get(job_id)


def run_ingestion_job(job_id: str, pdf_path: str, filename: str, settings: IngestionSettings) -> None:
    """Run ingestion for `job_id`, recording DONE + result or FAILED + error on the job record."""
    with _lock:
        _jobs[job_id].status = JobStatus.PROCESSING

    try:
        result = ingest_pdf(pdf_path, filename, settings)
    except Exception as exc:  # noqa: BLE001 - job failure is reported via status, not raised
        with _lock:
            _jobs[job_id].status = JobStatus.FAILED
            _jobs[job_id].error = str(exc)
        return

    with _lock:
        _jobs[job_id].status = JobStatus.DONE
        _jobs[job_id].result = result
