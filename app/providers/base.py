"""Base Provider Interfaces."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

class BaseTTSProvider(ABC):
    @abstractmethod
    def synthesize(self, text: str, voice: str, output_path: str) -> float:
        """Synthesize text into audio file at output_path. Return audio duration in seconds."""
        pass

    def synthesize_with_timestamps(
        self,
        text: str,
        voice: str,
        output_path: str,
    ) -> Tuple[float, List[Dict[str, Any]]]:
        """Synthesize text into audio file and return (duration, timestamps) if supported."""
        dur = self.synthesize(text, voice, output_path)
        return dur, []

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
        is_real_footage: bool = True,
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
        self.is_real_footage = is_real_footage

class BaseFootageProvider(ABC):
    @abstractmethod
    def search(
        self,
        query: str,
        min_duration: float = 3.0,
        avoid_terms: Optional[List[str]] = None,
        **kwargs,
    ) -> List[FootageCandidate]:
        """Search footage provider for matching commercial-clean clips."""
        pass

    @abstractmethod
    def download_clip(self, candidate: FootageCandidate, target_path: str) -> str:
        """Download footage clip to local disk."""
        pass

    def get_by_id(self, media_id: str) -> Optional[FootageCandidate]:
        """Fetch footage candidate directly by provider media ID if supported."""
        return None
