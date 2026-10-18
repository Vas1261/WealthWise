"""Deployment: the built frontend is served by the API on the same origin, without shadowing the API."""
import importlib

import pytest
from fastapi.testclient import TestClient

import app.main
from app.core.config import settings


@pytest.fixture
def served_app(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>WealthWise</title>")
    (tmp_path / "assets" / "app.js").write_text("console.log('ok')")
    monkeypatch.setattr(settings, "frontend_dist", str(tmp_path))
    yield importlib.reload(app.main).app
    monkeypatch.setattr(settings, "frontend_dist", "")
    importlib.reload(app.main)


@pytest.mark.usefixtures("migrated_db")
def test_frontend_and_api_share_one_origin(served_app):
    client = TestClient(served_app)
    assert "WealthWise" in client.get("/").text
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/health").json()["status"] == "ok"  # API routes are not shadowed
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/missing.js").status_code == 404


def test_without_a_build_the_api_serves_no_pages():
    assert TestClient(app.main.app).get("/").status_code == 404
