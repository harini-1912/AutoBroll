"""Pytest fixtures and configuration for IncuBrix testing."""
import os
import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

# Ensure test environment variables
os.environ["ENVIRONMENT"] = "testing"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["FOOTAGE_PRIMARY_PROVIDER"] = "mock"
os.environ["TTS_PROVIDER"] = "mock"

from app.core.config import settings
from app.core.database import Database, db
from app.main import app
from app.storage.local import LocalStorage

@pytest.fixture(autouse=True)
def setup_test_environment(monkeypatch, tmp_path):
    """Set up an isolated file-backed test database and storage directory."""
    test_db_file = str(tmp_path / "test.db")
    db.db_path = test_db_file
    if hasattr(db._local, "conn"):
        db._local.conn = None
    db.init_db()

    # Monkeypatch the global db instance in app.core.database and other modules
    monkeypatch.setattr("app.core.database.db", db)
    monkeypatch.setattr("app.api.v1.endpoints.jobs.db", db)
    monkeypatch.setattr("app.api.v1.endpoints.health.db", db)
    monkeypatch.setattr("app.workers.job_runner.db", db)

    # Isolated storage
    test_storage_dir = tmp_path / "storage"
    test_storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(test_storage_dir))
    monkeypatch.setattr("app.storage._storage_instance", LocalStorage(base_dir=str(test_storage_dir)))

    yield

@pytest.fixture
def client():
    """FastAPI TestClient fixture."""
    with TestClient(app) as test_client:
        yield test_client
