from alembic import command
from fastapi.testclient import TestClient

from app.main import app


def test_health_reports_ok(migrated_db):
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_reports_a_database_behind_the_migrations(migrated_db, alembic_config):
    """New code with `alembic upgrade head` not run yet: the database answers but tables are missing."""
    command.downgrade(alembic_config, "-1")
    response = TestClient(app).get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "migration_pending"}

    command.upgrade(alembic_config, "head")
    assert TestClient(app).get("/health").json() == {"status": "ok", "database": "ok"}


def test_health_reports_a_database_that_was_never_migrated():
    response = TestClient(app).get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "migration_pending"}
