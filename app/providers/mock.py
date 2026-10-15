"""Deterministic Mock Providers for no-key testing."""
import math
import struct
import wave
from pathlib import Path
from typing import List, Optional
from app.core.logging import logger
from app.providers.base import BaseFootageProvider, BaseTTSProvider, FootageCandidate

class MockTTSProvider(BaseTTSProvider):
    """Generates synthetic spoken audio waveforms using standard library wave module."""

    def synthesize(self, text: str, voice: str, output_path: str) -> float:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        # Estimate duration: ~150 words per minute -> 2.5 words per second
        words = text.split()
        duration = max(1.5, len(words) * 0.4)

        # Generate a gentle pure audio tone (440Hz / 880Hz) to represent speech
        sample_rate = 24000
        total_samples = int(sample_rate * duration)
        freq = 300.0 if "male" in voice else 440.0

        with wave.open(output_path, "wb") as wav_file:
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(sample_rate)

            # Generate audio data with fade in/out envelope
            frames = bytearray()
            for i in range(total_samples):
                t = float(i) / sample_rate
                # Envelope: avoid clicks at start/end
                envelope = min(1.0, i / 2400.0) * min(1.0, (total_samples - i) / 2400.0)
                # Modulated voice-like harmonic
                val = int(envelope * 16000.0 * (0.6 * math.sin(2 * math.pi * freq * t) + 0.4 * math.sin(2 * math.pi * (freq * 1.5) * t)))
                frames.extend(struct.pack("<h", val))

            wav_file.writeframes(frames)

        logger.info("MockTTS generated %.2fs audio for text '%s'", duration, text[:30])
        return duration

class MockFootageProvider(BaseFootageProvider):
    """Provides deterministic commercial-clean mock footage candidates."""

    def __init__(self, provider_name: str = "mock_stock"):
        self.provider_name = provider_name
        self._library = [
            FootageCandidate(
                clip_id="mock_clip_001",
                provider=self.provider_name,
                source_url="https://images.incubrix.test/footage/mock_001",
                download_url="https://images.incubrix.test/footage/mock_001.mp4",
                licence="CC0 / Commercial Free",
                attribution="Mock Stock Library",
                is_ai_generated=False,
                duration=10.0,
                width=1920,
                height=1080,
                fps=30.0,
                tags=["business", "office", "team", "growth", "value", "finance"],
            ),
            FootageCandidate(
                clip_id="mock_clip_002",
                provider=self.provider_name,
                source_url="https://images.incubrix.test/footage/mock_002",
                download_url="https://images.incubrix.test/footage/mock_002.mp4",
                licence="CC0 / Commercial Free",
                attribution="Mock Stock Library",
                is_ai_generated=False,
                duration=12.0,
                width=1920,
                height=1080,
                fps=30.0,
                tags=["technology", "code", "cloud", "server", "python", "developer"],
            ),
            FootageCandidate(
                clip_id="mock_clip_003",
                provider=self.provider_name,
                source_url="https://images.incubrix.test/footage/mock_003",
                download_url="https://images.incubrix.test/footage/mock_003.mp4",
                licence="CC0 / Commercial Free",
                attribution="Mock Stock Library",
                is_ai_generated=False,
                duration=8.0,
                width=1920,
                height=1080,
                fps=30.0,
                tags=["nature", "landscape", "calm", "trust", "sky", "outdoor"],
            ),
            FootageCandidate(
                clip_id="mock_clip_004",
                provider="ai_generative",
                source_url="https://images.incubrix.test/footage/ai_004",
                download_url="https://images.incubrix.test/footage/ai_004.mp4",
                licence="Proprietary AI / Commercial Clean",
                attribution="IncuBrix AI Motion Engine",
                is_ai_generated=True,
                duration=15.0,
                width=1920,
                height=1080,
                fps=30.0,
                tags=["abstract", "concept", "futuristic", "particles", "art"],
            ),
        ]

    def search(self, query: str, min_duration: float = 3.0, avoid_terms: Optional[List[str]] = None) -> List[FootageCandidate]:
        q_lower = query.lower()
        avoid = [a.lower() for a in (avoid_terms or [])]

        results = []
        for clip in self._library:
            # Check avoid terms
            if any(term in clip.tags for term in avoid):
                continue
            # Score match
            match_score = sum(1 for tag in clip.tags if tag in q_lower)
            if match_score > 0 or len(results) == 0:
                results.append(clip)

        if not results:
            results = [self._library[0]]
        return results

    def download_clip(self, candidate: FootageCandidate, target_path: str) -> str:
        # For unit testing, create a blank or synthetic file if it does not exist
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(b"\x00" * 1024)  # placeholder bytes for mock
        return str(target)
