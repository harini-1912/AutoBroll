"""MinIO / S3 Storage adapter conforming to Integration Standard #7."""
import io
from pathlib import Path
from typing import Optional, Union
from app.core.config import settings
from app.core.exceptions import StorageError
from app.core.logging import logger
from app.storage.base import BaseStorage
from app.storage.local import LocalStorage

class S3Storage(BaseStorage):
    """S3-compatible object storage (MinIO / AWS S3) with graceful local fallback."""

    def __init__(self):
        self.fallback = LocalStorage()
        self.client = None
        self.bucket = settings.S3_BUCKET_NAME
        self.enabled = False

        try:
            from minio import Minio
            # Parse endpoint (strip http:// or https://)
            endpoint = settings.S3_ENDPOINT_URL.replace("http://", "").replace("https://", "").rstrip("/")
            self.client = Minio(
                endpoint,
                access_key=settings.S3_ACCESS_KEY,
                secret_key=settings.S3_SECRET_KEY,
                secure=settings.S3_USE_SSL,
            )
            # Check or create bucket if reachable
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
            self.enabled = True
            logger.info("MinIO / S3 storage initialized successfully on bucket '%s'", self.bucket)
        except Exception as e:
            logger.warning(
                "Could not connect to MinIO/S3 at %s: %s. Falling back to local storage.",
                settings.S3_ENDPOINT_URL,
                str(e),
            )
            self.enabled = False

    def save_bytes(self, relative_path: str, data: bytes, content_type: Optional[str] = None) -> str:
        clean_rel = relative_path.lstrip("/\\").replace("\\", "/")
        if not self.enabled or self.client is None:
            return self.fallback.save_bytes(clean_rel, data, content_type)

        try:
            stream = io.BytesIO(data)
            self.client.put_object(
                bucket_name=self.bucket,
                object_name=clean_rel,
                data=stream,
                length=len(data),
                content_type=content_type or "application/octet-stream",
            )
            return self.get_url(clean_rel)
        except Exception as e:
            logger.error("S3 upload failed for '%s', falling back to local: %s", clean_rel, str(e))
            return self.fallback.save_bytes(clean_rel, data, content_type)

    def save_file(self, relative_path: str, source_path: Union[str, Path], content_type: Optional[str] = None) -> str:
        clean_rel = relative_path.lstrip("/\\").replace("\\", "/")
        if not self.enabled or self.client is None:
            return self.fallback.save_file(clean_rel, source_path, content_type)

        source = Path(source_path)
        if not source.exists():
            raise StorageError(f"Source file not found: {source_path}")

        try:
            self.client.fput_object(
                bucket_name=self.bucket,
                object_name=clean_rel,
                file_path=str(source),
                content_type=content_type or "application/octet-stream",
            )
            return self.get_url(clean_rel)
        except Exception as e:
            logger.error("S3 fput failed for '%s', falling back to local: %s", clean_rel, str(e))
            return self.fallback.save_file(clean_rel, source_path, content_type)

    def get_file_bytes(self, relative_path: str) -> bytes:
        clean_rel = relative_path.lstrip("/\\").replace("\\", "/")
        if not self.enabled or self.client is None:
            return self.fallback.get_file_bytes(clean_rel)

        try:
            response = self.client.get_object(self.bucket, clean_rel)
            data = response.read()
            response.close()
            response.release_conn()
            return data
        except Exception:
            return self.fallback.get_file_bytes(clean_rel)

    def get_url(self, relative_path: str) -> str:
        clean_rel = relative_path.lstrip("/\\").replace("\\", "/")
        if not self.enabled or self.client is None:
            return self.fallback.get_url(clean_rel)

        # For MinIO in local dev, provide direct download url through app proxy or direct S3 endpoint
        return f"{settings.BASE_URL}/api/v1/jobs/download/{clean_rel}"

    def exists(self, relative_path: str) -> bool:
        clean_rel = relative_path.lstrip("/\\").replace("\\", "/")
        if not self.enabled or self.client is None:
            return self.fallback.exists(clean_rel)

        try:
            self.client.stat_object(self.bucket, clean_rel)
            return True
        except Exception:
            return self.fallback.exists(clean_rel)
