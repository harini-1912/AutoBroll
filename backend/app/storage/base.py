"""Base Storage Interface."""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Optional, Union

class BaseStorage(ABC):
    """Abstract storage backend for uploads and generated video outputs."""

    @abstractmethod
    def save_bytes(self, relative_path: str, data: bytes, content_type: Optional[str] = None) -> str:
        """Save raw bytes to storage and return accessible URL/path."""
        pass

    @abstractmethod
    def save_file(self, relative_path: str, source_path: Union[str, Path], content_type: Optional[str] = None) -> str:
        """Save a local file to storage and return accessible URL/path."""
        pass

    @abstractmethod
    def get_file_bytes(self, relative_path: str) -> bytes:
        """Retrieve file bytes."""
        pass

    @abstractmethod
    def get_url(self, relative_path: str) -> str:
        """Get external download URL."""
        pass

    @abstractmethod
    def exists(self, relative_path: str) -> bool:
        """Check if file exists in storage."""
        pass
