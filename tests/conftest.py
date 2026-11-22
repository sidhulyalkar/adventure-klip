import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))


import pytest as _pytest_lock


@_pytest_lock.fixture(autouse=True)
def _private_model_lock(tmp_path_factory, monkeypatch):
    """Tests never touch the machine-wide model lock in ~/.klip."""
    monkeypatch.setenv("KLIP_LOCK_DIR", str(tmp_path_factory.mktemp("locks")))
