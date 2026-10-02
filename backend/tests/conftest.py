import os
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

# Point the app at a throwaway database before any app module creates its engine.
_db_file = Path(tempfile.mkdtemp()) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
def alembic_config() -> Config:
    return Config(str(BACKEND_DIR / "alembic.ini"))


@pytest.fixture
def migrated_db(alembic_config):
    command.upgrade(alembic_config, "head")
    yield
    command.downgrade(alembic_config, "base")
