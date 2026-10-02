import logging
import os
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

# Point the app at a throwaway database before any app module creates its engine.
_db_file = Path(tempfile.mkdtemp()) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ["APP_ENV"] = "test"
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 40
# The test client talks plain HTTP, which never sends Secure cookies back.
os.environ["COOKIE_SECURE"] = "false"
# Offline by default: blank the Twelve Data key so no test can use the real one from .env or reach
# the network. Only an explicit live run (RUN_LIVE_TWELVE_DATA=1) keeps the configured key.
LIVE_TWELVE_DATA = os.environ.get("RUN_LIVE_TWELVE_DATA") == "1"
if not LIVE_TWELVE_DATA:
    os.environ["TWELVE_DATA_API_KEY"] = ""

BACKEND_DIR = Path(__file__).resolve().parent.parent


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live_twelve_data: calls the real Twelve Data API; needs RUN_LIVE_TWELVE_DATA=1 and a key"
    )


@pytest.fixture
def alembic_config() -> Config:
    return Config(str(BACKEND_DIR / "alembic.ini"))


def _reenable_app_loggers() -> None:
    # Alembic's fileConfig() disables loggers that already exist; keep the app's (and httpx2's)
    # loggers working so log-content assertions are never vacuous.
    for name, logger in logging.root.manager.loggerDict.items():
        if isinstance(logger, logging.Logger) and name.split(".")[0] in ("app", "httpx2"):
            logger.disabled = False


@pytest.fixture(autouse=True)
def _loggers_enabled():
    _reenable_app_loggers()


@pytest.fixture
def migrated_db(alembic_config):
    command.upgrade(alembic_config, "head")
    _reenable_app_loggers()
    yield
    command.downgrade(alembic_config, "base")
