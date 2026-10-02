from fastapi.testclient import TestClient

from app.main import app


def test_health_reports_ok(migrated_db):
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
