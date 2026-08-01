# tests/ingestion/test_router.py
import io
import time

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _read_fixture_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _poll_until_done(job_id: str, timeout_seconds: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/ingestion/jobs/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.1)
    raise TimeoutError(f"job {job_id} did not finish within {timeout_seconds}s")


def test_upload_pdf_rejects_non_pdf_content_type():
    response = client.post(
        "/ingestion/pdf",
        files={"file": ("notes.txt", io.BytesIO(b"just text"), "text/plain")},
    )
    assert response.status_code == 400


def test_upload_pdf_rejects_file_without_pdf_header():
    response = client.post(
        "/ingestion/pdf",
        files={"file": ("fake.pdf", io.BytesIO(b"not really a pdf"), "application/pdf")},
    )
    assert response.status_code == 400


def test_upload_and_poll_simple_pdf(simple_text_pdf):
    pdf_bytes = _read_fixture_bytes(simple_text_pdf)
    response = client.post(
        "/ingestion/pdf",
        files={"file": ("simple.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    final = _poll_until_done(job_id)
    assert final["status"] == "done"
    assert final["result"]["chunks"]
    assert final["result"]["chunks"][0]["source_filename"] == "simple.pdf"


def test_get_job_status_404_for_unknown_job():
    response = client.get("/ingestion/jobs/does-not-exist")
    assert response.status_code == 404
