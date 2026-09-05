from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_metrics_endpoint_is_mounted_and_returns_prometheus_text_format():
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
