"""Health and Readiness endpoints conforming to Integration Standard #8."""
from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from app.core.config import settings
from app.core.database import db
from app.storage import get_storage

router = APIRouter(tags=["Health"])

class HealthResponse(BaseModel):
    status: str
    version: str
    app_name: str

class ReadinessResponse(BaseModel):
    status: str
    database: str
    storage: str
    ffmpeg: str
    version: str

@router.get("/health", response_model=HealthResponse)
def health_check():
    """Liveness probe: verifies service process is running."""
    return HealthResponse(
        status="healthy",
        version=settings.APP_VERSION,
        app_name=settings.APP_NAME,
    )

@router.get("/ready", response_model=ReadinessResponse)
def readiness_check(response: Response):
    """Readiness probe: verifies database, storage, and media tools."""
    # Check Database
    db_ok = False
    try:
        conn = db.get_connection()
        conn.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        db_ok = False

    # Check Storage
    storage_ok = False
    try:
        storage = get_storage()
        test_key = "health_check_probe.tmp"
        storage.save_bytes(test_key, b"ok")
        storage_ok = storage.exists(test_key)
    except Exception:
        storage_ok = False

    # Check FFmpeg
    ffmpeg_ok = bool(settings.FFMPEG_PATH and len(settings.FFMPEG_PATH) > 0)

    is_ready = db_ok and storage_ok and ffmpeg_ok
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        database="connected" if db_ok else "disconnected",
        storage="available" if storage_ok else "unavailable",
        ffmpeg="available" if ffmpeg_ok else "missing",
        version=settings.APP_VERSION,
    )
