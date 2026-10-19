"""Deterministic Mock Providers for no-key testing."""
import math
import re
import struct
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from app.core.logging import logger
from app.providers.base import BaseFootageProvider, BaseTTSProvider, FootageCandidate

class MockTTSProvider(BaseTTSProvider):
    """Generates synthetic spoken audio waveforms with sentence timestamps and inter-sentence pauses."""

    def __init__(self):
        self.last_timestamps: List[Dict[str, Any]] = []

    def synthesize_with_timestamps(self, text: str, voice: str, output_path: str) -> Tuple[float, List[Dict[str, Any]]]:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
        if not sentences:
            sentences = [text.strip() or "Default narration."]

        sample_rate = 24000
        freq = 300.0 if "male" in voice else 440.0
        pause_samples = int(sample_rate * 0.3)  # 300ms natural pause between sentences

        all_frames = bytearray()
        timestamps: List[Dict[str, Any]] = []
        curr_time = 0.0

        for idx, sentence in enumerate(sentences):
            words = sentence.split()
            sentence_dur = max(1.0, round(len(words) * 0.45, 2))
            st = round(curr_time, 2)
            et = round(st + sentence_dur, 2)

            num_speech_samples = int(sample_rate * sentence_dur)
            for i in range(num_speech_samples):
                t = float(i) / sample_rate
                envelope = min(1.0, i / 2400.0) * min(1.0, (num_speech_samples - i) / 2400.0)
                val = int(envelope * 16000.0 * (0.6 * math.sin(2 * math.pi * freq * t) + 0.4 * math.sin(2 * math.pi * (freq * 1.5) * t)))
                all_frames.extend(struct.pack("<h", val))

            timestamps.append({
                "text": sentence,
                "start_time": st,
                "end_time": et,
                "duration": round(et - st, 2),
                "method": "tts_timestamps",
                "confidence": 0.95,
            })
            curr_time = et

            if idx < len(sentences) - 1:
                all_frames.extend(b"\x00\x00" * pause_samples)
                curr_time = round(curr_time + 0.3, 2)

        total_duration = round(len(all_frames) / (sample_rate * 2), 2)
        if timestamps:
            timestamps[-1]["end_time"] = total_duration
            timestamps[-1]["duration"] = round(total_duration - timestamps[-1]["start_time"], 2)

        with wave.open(output_path, "wb") as wav_file:
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(all_frames)

        self.last_timestamps = timestamps
        logger.info("MockTTS generated %.2fs audio with %d sentence timestamps for '%s'", total_duration, len(timestamps), text[:30])
        return total_duration, timestamps

    def synthesize(self, text: str, voice: str, output_path: str) -> float:
        dur, _ = self.synthesize_with_timestamps(text, voice, output_path)
        return dur

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
                is_real_footage=False,
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
                is_real_footage=False,
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
                is_real_footage=False,
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
                is_real_footage=False,
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
