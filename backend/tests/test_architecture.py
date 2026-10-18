"""Runs the import-linter contracts in .importlinter (business-layer purity, NFR-09, and layer order)."""
import os
from pathlib import Path

from importlinter.cli import lint_imports

BACKEND = Path(__file__).resolve().parents[1]


def test_architecture_contracts_hold(monkeypatch):
    monkeypatch.chdir(BACKEND)
    monkeypatch.setenv("PYTHONPATH", str(BACKEND) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    assert lint_imports(config_filename=".importlinter", no_cache=True) == 0
