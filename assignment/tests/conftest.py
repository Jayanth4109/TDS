import pytest
from fastapi.testclient import TestClient
from agents.app import app


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DB", str(tmp_path / "state.sqlite3"))


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)
