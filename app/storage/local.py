"""Local Filesystem Storage implementation."""
import shutil
from pathlib import Path
from typing import Optional, Union
from app.core.config import settings
from app.core.exceptions import StorageError
from app.storage.base import BaseStorage

class LocalStorage(BaseStorage):
    """Stores files locally on the filesystem and returns HTTP download URLs."""

    def __init__(self, base_dir: Optional[str] = None, base_url: Optional[str] = None):
        self.base_dir = Path(base_dir or settings.LOCAL_STORAGE_DIR).resolve()
        self.base_url = (base_url or settings.BASE_URL).rstrip("/")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_target_path(self, relative_path: str) -> Path:
        # Sanitize path to prevent directory traversal
        clean_rel = relative_path.lstrip("/\\").replace("..", "")
        target = (self.base_dir / clean_rel).resolve()
        if not str(target).startswith(str(self.base_dir)):
            raise StorageError(f"Invalid path traversal attempt: {relative_path}")
        return target

    def save_bytes(self, relative_path: str, data: bytes, content_type: Optional[str] = None) -> str:
        target = self._get_target_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_bytes(data)
            return self.get_url(relative_path)
        except Exception as e:
            raise StorageError(f"Failed to write bytes to local storage: {str(e)}")

    def save_file(self, relative_path: str, source_path: Union[str, Path], content_type: Optional[str] = None) -> str:
        source = Path(source_path)
        if not source.exists():
            raise StorageError(f"Source file not found: {source_path}")
        target = self._get_target_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, target)
            return self.get_url(relative_path)
        except Exception as e:
            raise StorageError(f"Failed to copy file to local storage: {str(e)}")

    def get_file_bytes(self, relative_path: str) -> bytes:
        target = self._get_target_path(relative_path)
        if not target.exists():
            raise StorageError(f"File not found: {relative_path}")
        return target.read_bytes()

    def get_url(self, relative_path: str) -> str:
        clean_rel = relative_path.lstrip("/\\").replace("\\", "/")
        return f"{self.base_url}/api/v1/jobs/download/{clean_rel}"

    def exists(self, relative_path: str) -> bool:
        target = self._get_target_path(relative_path)
        return target.exists()
