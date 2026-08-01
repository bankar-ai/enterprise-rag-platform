# tests/ingestion/test_router.py
import io
import time

from fastapi.testclient import TestClient

from app.ingestion.config import get_settings
from app.main import app

client = TestClient(app)
_PDF_MAGIC = b"%PDF-"


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


def test_upload_and_poll_multi_paragraph_pdf(multi_paragraph_pdf):
    # End-to-end regression test for a Critical whole-branch-review finding: every existing
    # fixture used single-line sections, which accidentally masked a chunker bug where
    # MarkdownHeaderTextSplitter's reconstructed (whitespace-normalized) section.page_content
    # broke substring-offset-based page tracking for any section spanning more than one line
    # — i.e. essentially all real PDFs. This exercises the full upload -> parse -> chunk
    # pipeline against a realistic multi-paragraph, multi-line fixture.
    pdf_bytes = _read_fixture_bytes(multi_paragraph_pdf)
    response = client.post(
        "/ingestion/pdf",
        files={"file": ("multi_paragraph.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    final = _poll_until_done(job_id)
    assert final["status"] == "done"
    chunks = final["result"]["chunks"]
    assert chunks

    combined = " ".join(c["text"] for c in chunks)
    assert "First paragraph of the intro" in combined
    assert "Second paragraph after a visual gap" in combined

    for chunk in chunks:
        assert chunk["text"].strip()
        assert chunk["section_path"] == ["Introduction"]
        assert chunk["page_start"] == 1
        assert chunk["page_end"] == 1
        # No splitter implementation artifacts should leak into chunk text.
        assert "  \n" not in chunk["text"]


def test_get_job_status_404_for_unknown_job():
    response = client.get("/ingestion/jobs/does-not-exist")
    assert response.status_code == 404


def test_upload_pdf_rejects_oversized_file(monkeypatch):
    # Finding 3 (final whole-branch review): the upload endpoint streamed uploads of
    # unbounded size to a temp file with no size check — a resource-exhaustion risk.
    # A tiny configured limit lets this test exercise the guard without a huge fixture.
    monkeypatch.setenv("INGESTION_MAX_UPLOAD_SIZE_BYTES", "10")
    get_settings.cache_clear()
    try:
        oversized_body = _PDF_MAGIC + b"0" * 100
        response = client.post(
            "/ingestion/pdf",
            files={"file": ("big.pdf", io.BytesIO(oversized_body), "application/pdf")},
        )
        assert response.status_code == 413
    finally:
        get_settings.cache_clear()
