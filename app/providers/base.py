"""Base Provider Interfaces."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

class BaseTTSProvider(ABC):
    @abstractmethod
    def synthesize(self, text: str, voice: str, output_path: str) -> float:
        """Synthesize text into audio file at output_path. Return audio duration in seconds."""
        pass

class FootageCandidate:
    def __init__(
        self,
        clip_id: str,
        provider: str,
        source_url: str,
        download_url: str,
        licence: str,
        attribution: str,
        is_ai_generated: bool,
        duration: float,
        width: int,
        height: int,
        fps: float,
        tags: List[str],
        thumbnail_url: Optional[str] = None,
    ):
        self.clip_id = clip_id
        self.provider = provider
        self.source_url = source_url
        self.download_url = download_url
        self.licence = licence
        self.attribution = attribution
        self.is_ai_generated = is_ai_generated
        self.duration = duration
        self.width = width
        self.height = height
        self.fps = fps
        self.tags = tags
        self.thumbnail_url = thumbnail_url

class BaseFootageProvider(ABC):
    @abstractmethod
    def search(self, query: str, min_duration: float = 3.0, avoid_terms: Optional[List[str]] = None) -> List[FootageCandidate]:
        """Search footage provider for matching commercial-clean clips."""
        pass

    @abstractmethod
    def download_clip(self, candidate: FootageCandidate, target_path: str) -> str:
        """Download footage clip to local disk."""
        pass
