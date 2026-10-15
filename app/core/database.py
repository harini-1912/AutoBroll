"""Persistent SQLite database and Job Store conforming to Integration Standard #3 & #4."""
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from app.core.config import settings
from app.core.logging import logger

class Database:
    """Thread-safe SQLite database manager for job persistence and idempotency."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # Extract file path from sqlite:///./data/incubrix.db
            raw_url = settings.DATABASE_URL
            if raw_url.startswith("sqlite:///"):
                self.db_path = raw_url.replace("sqlite:///", "")
            else:
                self.db_path = "./data/incubrix.db"
        else:
            self.db_path = db_path

        # Ensure directory exists if not in-memory
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._local = threading.local()
        self._lock = threading.Lock()
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # Enable WAL mode for concurrent read/write performance
            if self.db_path != ":memory:":
                conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            self._local.conn = conn
        return self._local.conn

    def init_db(self) -> None:
        """Create necessary tables if they don't exist."""
        conn = self.get_connection()
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    idempotency_key TEXT,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    stage TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    request_payload TEXT NOT NULL,
                    result_urls TEXT,
                    error_payload TEXT,
                    warnings TEXT,
                    timeline TEXT,
                    manifest TEXT
                );

                CREATE TABLE IF NOT EXISTS idempotency (
                    idempotency_key TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    response_code INTEGER NOT NULL,
                    response_body TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
                CREATE INDEX IF NOT EXISTS idx_jobs_idempotency_key ON jobs(idempotency_key);
                """
            )

    # --- Job Persistence Methods ---

    def create_job(self, job_id: str, request_data: Dict[str, Any], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        conn = self.get_connection()
        with self._lock, conn:
            conn.execute(
                """
                INSERT INTO jobs (job_id, idempotency_key, status, progress, stage, created_at, request_payload, warnings)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    idempotency_key,
                    "queued",
                    0,
                    "queued",
                    now,
                    json.dumps(request_data),
                    json.dumps([]),
                ),
            )
        return self.get_job(job_id)  # type: ignore

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def update_job_progress(self, job_id: str, status: str, progress: int, stage: str, warnings: Optional[List[str]] = None) -> None:
        conn = self.get_connection()
        with self._lock, conn:
            if warnings is not None:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, progress = ?, stage = ?, warnings = ?
                    WHERE job_id = ?
                    """,
                    (status, progress, stage, json.dumps(warnings), job_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, progress = ?, stage = ?
                    WHERE job_id = ?
                    """,
                    (status, progress, stage, job_id),
                )

    def complete_job_success(
        self,
        job_id: str,
        result_urls: Dict[str, str],
        timeline: Dict[str, Any],
        manifest: Dict[str, Any],
        warnings: Optional[List[str]] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self.get_connection()
        with self._lock, conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'succeeded', progress = 100, stage = 'completed', completed_at = ?,
                    result_urls = ?, timeline = ?, manifest = ?, warnings = ?
                WHERE job_id = ?
                """,
                (
                    now,
                    json.dumps(result_urls),
                    json.dumps(timeline),
                    json.dumps(manifest),
                    json.dumps(warnings or []),
                    job_id,
                ),
            )

    def complete_job_failure(self, job_id: str, error_payload: Dict[str, Any], stage: str = "failed") -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self.get_connection()
        with self._lock, conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed', stage = ?, completed_at = ?, error_payload = ?
                WHERE job_id = ?
                """,
                (stage, now, json.dumps(error_payload), job_id),
            )

    # --- Idempotency Methods ---

    def get_idempotency_record(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM idempotency WHERE idempotency_key = ?", (idempotency_key,))
        row = cursor.fetchone()
        if not row:
            return None
        return dict(row)

    def save_idempotency_record(
        self, idempotency_key: str, job_id: str, request_hash: str, response_code: int, response_body: str
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self.get_connection()
        with self._lock, conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO idempotency (idempotency_key, job_id, request_hash, created_at, response_code, response_body)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (idempotency_key, job_id, request_hash, now, response_code, response_body),
            )

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        for json_col in ("request_payload", "result_urls", "error_payload", "warnings", "timeline", "manifest"):
            if d.get(json_col):
                try:
                    d[json_col] = json.loads(d[json_col])
                except Exception:
                    pass
        return d

# Global database instance
db = Database()
