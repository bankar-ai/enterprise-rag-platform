"""In-memory async ingestion job tracking (no persistent queue; single-process only)."""

import logging
import threading
import uuid

from app.core.telemetry import get_meter
from app.embedding.client import EmbeddingClient
from app.embedding.index import FaissIndex
from app.embedding.service import embed_and_persist
from app.ingestion.config import IngestionSettings
from app.ingestion.schemas import IngestResponse, JobStatus
from app.ingestion.service import ingest_pdf

logger = logging.getLogger(__name__)

_jobs_counter = get_meter().create_counter(
    "ingestion_jobs_total", description="Completed ingestion jobs by outcome"
)

_jobs: dict[str, "JobRecord"] = {}
_lock = threading.Lock()


class JobRecord:
    """Mutable state for one tracked ingestion job."""

    def __init__(self, owner_id: uuid.UUID) -> None:
        """Initialize a new job in PENDING status, owned by `owner_id`, with no result or error yet."""
        self.owner_id = owner_id
        self.status: JobStatus = JobStatus.PENDING
        self.result: IngestResponse | None = None
        self.error: str | None = None


def create_job(owner_id: uuid.UUID) -> str:
    """Register a new PENDING job owned by `owner_id` and return its ID."""
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = JobRecord(owner_id)
    return job_id


def get_job(job_id: str) -> JobRecord | None:
    """Look up a job by ID, or None if it doesn't exist."""
    with _lock:
        return _jobs.get(job_id)


def run_ingestion_job(
    job_id: str,
    pdf_path: str,
    filename: str,
    settings: IngestionSettings,
    owner_id: uuid.UUID,
    embedding_client: EmbeddingClient | None = None,
    faiss_index: FaissIndex | None = None,
) -> None:
    """Run ingestion for `job_id`, recording DONE + result or FAILED + error on the job record.

    On success, also embeds and durably persists the resulting chunks (Postgres + FAISS),
    stamping `owner_id` as the resulting document's owner — a DONE job means the data is
    embedded and persisted, not just held in memory.
    """
    with _lock:
        _jobs[job_id].status = JobStatus.PROCESSING

    try:
        result = ingest_pdf(pdf_path, filename, settings)
        embed_and_persist(
            document_id=result.document_id,
            source_filename=filename,
            chunks=result.chunks,
            owner_id=owner_id,
            embedding_client=embedding_client,
            faiss_index=faiss_index,
        )
    except Exception as exc:  # noqa: BLE001 - job failure is reported via status, not raised
        logger.exception("Ingestion job %s failed for file %r", job_id, filename)
        with _lock:
            _jobs[job_id].status = JobStatus.FAILED
            _jobs[job_id].error = str(exc)
        _jobs_counter.add(1, {"status": "failed"})
        return

    with _lock:
        _jobs[job_id].status = JobStatus.DONE
        _jobs[job_id].result = result
    _jobs_counter.add(1, {"status": "done"})
