import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile, status

from app.ingestion import jobs
from app.ingestion.config import get_settings
from app.ingestion.schemas import JobStatusResponse

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

_PDF_MAGIC = b"%PDF-"


@router.post("/pdf", status_code=status.HTTP_202_ACCEPTED)
async def upload_pdf(file: UploadFile = File(...), background_tasks: BackgroundTasks = None) -> dict:
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="File must be a PDF (content-type application/pdf)")

    header = await file.read(5)
    if header != _PDF_MAGIC:
        raise HTTPException(status_code=400, detail="File is not a valid PDF (missing %PDF- header)")
    await file.seek(0)

    tmp_dir = Path(tempfile.mkdtemp())
    tmp_path = tmp_dir / file.filename
    with tmp_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    job_id = jobs.create_job()
    settings = get_settings()
    background_tasks.add_task(jobs.run_ingestion_job, job_id, str(tmp_path), file.filename, settings)

    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def get_job_status(job_id: str) -> JobStatusResponse:
    record = jobs.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(status=record.status, result=record.result, error=record.error)
