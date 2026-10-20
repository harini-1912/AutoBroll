"""Application Configuration Module."""
import os
import shutil
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load environment variables from .env file
load_dotenv()

def _resolve_ffmpeg_path() -> str:
    """Find system ffmpeg or fallback to imageio-ffmpeg binary."""
    custom = os.getenv("FFMPEG_PATH")
    if custom and Path(custom).exists():
        return custom
    system_path = shutil.which("ffmpeg")
    if system_path:
        return system_path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"

def _resolve_ffprobe_path() -> str:
    """Find system ffprobe or locate it next to ffmpeg."""
    custom = os.getenv("FFPROBE_PATH")
    if custom and Path(custom).exists():
        return custom
    system_path = shutil.which("ffprobe")
    if system_path:
        return system_path
    # Check if ffprobe is in the same directory as ffmpeg
    ffmpeg_exe = _resolve_ffmpeg_path()
    if ffmpeg_exe and Path(ffmpeg_exe).is_file():
        probe_candidate = Path(ffmpeg_exe).parent / "ffprobe.exe"
        if probe_candidate.exists():
            return str(probe_candidate)
        probe_unix = Path(ffmpeg_exe).parent / "ffprobe"
        if probe_unix.exists():
            return str(probe_unix)
    return "ffprobe"

class Settings(BaseModel):
    # Environment
    ENVIRONMENT: str = Field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    PORT: int = Field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    HOST: str = Field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    LOG_LEVEL: str = Field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    APP_NAME: str = Field(default_factory=lambda: os.getenv("APP_NAME", "IncuBrix B-Roll Video Engine"))
    APP_VERSION: str = Field(default_factory=lambda: os.getenv("APP_VERSION", "1.0.0"))

    # Security & Limits
    DEMO_API_KEY: str = Field(default_factory=lambda: os.getenv("DEMO_API_KEY", ""))
    MAX_UPLOAD_SIZE_MB: int = Field(default_factory=lambda: int(os.getenv("MAX_UPLOAD_SIZE_MB", "50")))
    MAX_AUDIO_DURATION_SECONDS: int = Field(default_factory=lambda: int(os.getenv("MAX_AUDIO_DURATION_SECONDS", "600")))
    RATE_LIMIT_PER_MINUTE: int = Field(default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_MINUTE", "60")))
    WEBHOOK_SIGNATURE_SECRET: str = Field(default_factory=lambda: os.getenv("WEBHOOK_SIGNATURE_SECRET", "incubrix_secret"))

    # Storage
    STORAGE_BACKEND: str = Field(default_factory=lambda: os.getenv("STORAGE_BACKEND", "local"))
    LOCAL_STORAGE_DIR: str = Field(default_factory=lambda: os.getenv("LOCAL_STORAGE_DIR", "./storage"))
    ARTIFACTS_DIR: str = Field(default_factory=lambda: os.getenv("ARTIFACTS_DIR", os.getenv("LOCAL_STORAGE_DIR", "./storage")))
    BASE_URL: str = Field(default_factory=lambda: os.getenv("BASE_URL", "http://localhost:8000"))

    # S3 / MinIO
    S3_ENDPOINT_URL: str = Field(default_factory=lambda: os.getenv("S3_ENDPOINT_URL", "http://localhost:9000"))
    S3_ACCESS_KEY: str = Field(default_factory=lambda: os.getenv("S3_ACCESS_KEY", "minioadmin"))
    S3_SECRET_KEY: str = Field(default_factory=lambda: os.getenv("S3_SECRET_KEY", "minioadmin"))
    S3_BUCKET_NAME: str = Field(default_factory=lambda: os.getenv("S3_BUCKET_NAME", "incubrix-videos"))
    S3_REGION: str = Field(default_factory=lambda: os.getenv("S3_REGION", "us-east-1"))
    S3_USE_SSL: bool = Field(default_factory=lambda: os.getenv("S3_USE_SSL", "false").lower() == "true")

    # Database
    DATABASE_URL: str = Field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./data/incubrix.db"))

    # Media Binaries
    FFMPEG_PATH: str = Field(default_factory=_resolve_ffmpeg_path)
    FFPROBE_PATH: str = Field(default_factory=_resolve_ffprobe_path)
    FFMPEG_THREADS: int = Field(default_factory=lambda: int(os.getenv("FFMPEG_THREADS", "2")))

    # Providers
    FOOTAGE_PRIMARY_PROVIDER: str = Field(default_factory=lambda: os.getenv("FOOTAGE_PRIMARY_PROVIDER", "pixabay"))
    PIXABAY_API_KEY: Optional[str] = Field(default_factory=lambda: os.getenv("PIXABAY_API_KEY") or "58096074-b7197b00a8b5a59653d71b5e3")
    PEXELS_API_KEY: Optional[str] = Field(default_factory=lambda: os.getenv("PEXELS_API_KEY") or None)
    POLLINATIONS_API_KEY: Optional[str] = Field(default_factory=lambda: os.getenv("POLLINATIONS_API_KEY") or "sk_GAvJaRf8drlVtRQwBNdHUKWYMdgIFf70")
    REPLICATE_API_TOKEN: Optional[str] = Field(default_factory=lambda: os.getenv("REPLICATE_API_TOKEN") or None)

    # TTS
    TTS_PROVIDER: str = Field(default_factory=lambda: os.getenv("TTS_PROVIDER", "gtts"))
    DEFAULT_VOICE: str = Field(default_factory=lambda: os.getenv("DEFAULT_VOICE", "en-IN-female"))

    # Concurrency & Retention
    MAX_CONCURRENT_JOBS: int = Field(default_factory=lambda: int(os.getenv("MAX_CONCURRENT_JOBS", "2")))
    JOB_RETENTION_HOURS: int = Field(default_factory=lambda: int(os.getenv("JOB_RETENTION_HOURS", "24")))

    def ensure_directories(self) -> None:
        """Create necessary data, storage, and temp directories."""
        Path(self.LOCAL_STORAGE_DIR).mkdir(parents=True, exist_ok=True)
        Path("./data").mkdir(parents=True, exist_ok=True)
        Path("./temp").mkdir(parents=True, exist_ok=True)

settings = Settings()
settings.ensure_directories()
