"""Footage and TTS Providers Module."""
from typing import Optional
from app.core.config import settings
from app.providers.base import BaseFootageProvider, BaseTTSProvider, FootageCandidate
from app.providers.gtts_provider import GTTSProvider
from app.providers.mock import MockFootageProvider, MockTTSProvider
from app.providers.pexels import PexelsFootageProvider
from app.providers.pixabay import PixabayFootageProvider


def get_tts_provider(provider_name: Optional[str] = None) -> BaseTTSProvider:
    """Factory to instantiate configured or requested TTS provider.

    Supported values:
      - 'mock', 'test': Deterministic offline waveform generator.
      - 'gtts', 'google': Real Google Text-to-Speech (requires internet connectivity).

    Raises:
      - ValueError: If provider is missing, empty, invalid, or unsupported.
    """
    raw = provider_name if provider_name is not None else settings.TTS_PROVIDER
    if raw is None or not str(raw).strip():
        raise ValueError("TTS_PROVIDER is not configured or is empty. Supported providers: 'gtts', 'mock'.")
    name = str(raw).strip().lower()
    if name in ("mock", "test"):
        return MockTTSProvider()
    elif name in ("gtts", "google"):
        return GTTSProvider()
    else:
        raise ValueError(f"Unsupported TTS_PROVIDER: '{raw}'. Supported providers: 'gtts', 'mock'.")


__all__ = [
    "BaseFootageProvider",
    "BaseTTSProvider",
    "FootageCandidate",
    "GTTSProvider",
    "MockFootageProvider",
    "MockTTSProvider",
    "PexelsFootageProvider",
    "PixabayFootageProvider",
    "get_tts_provider",
]
