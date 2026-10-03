import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    """Every test gets its own data folder: live books and sandboxes are never touched."""
    import config
    data = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "LIVE_DIR", data / "live")
    monkeypatch.setattr(config, "SANDBOX_DIR", data / "sandbox")
    monkeypatch.setattr(config, "ACTIVE_FILE", data / "active.json")
    monkeypatch.setattr(config, "INBOX_DIR", tmp_path / "Inbox")
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path / "feedback")
    for d in (data / "live", data / "sandbox", tmp_path / "Inbox", tmp_path / "results", tmp_path / "feedback"):
        d.mkdir(parents=True, exist_ok=True)
    yield data


@pytest.fixture
def biz():
    from books import business
    return business.create_sandbox("t1", ai_mode="replay")
