"""Real Google Text-to-Speech (gTTS) Provider conforming to MH-1."""
import os
import re
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.logging import logger
from app.providers.base import BaseTTSProvider
from app.rendering.ffmpeg_engine import probe_media

VOICE_MAP = {
    "en-in-female": ("en", "co.in"),
    "en-in-male": ("en", "co.in"),
    "en-us-female": ("en", "com"),
    "en-us-male": ("en", "com"),
    "en-gb-female": ("en", "co.uk"),
    "en-gb-male": ("en", "co.uk"),
    "hi-in-female": ("hi", "co.in"),
    "hi-in-male": ("hi", "co.in"),
    "ta-in-female": ("ta", "co.in"),
    "ta-in-male": ("ta", "co.in"),
    "te-in-female": ("te", "co.in"),
    "te-in-male": ("te", "co.in"),
}


class GTTSProvider(BaseTTSProvider):
    """Real Text-to-Speech provider using Google Text-to-Speech (gTTS).

    NOTE: gTTS requires network/internet connectivity to Google's TTS endpoints.
    """

    def __init__(self, default_lang: str = "en", default_tld: str = "co.in"):
        self.default_lang = default_lang
        self.default_tld = default_tld
        self.last_timestamps: List[Dict[str, Any]] = []

    def _resolve_voice_params(self, voice: Optional[str]) -> Tuple[str, str]:
        """Map project voice string to (lang, tld)."""
        if not voice:
            return self.default_lang, self.default_tld

        v_lower = voice.lower().strip()
        if v_lower in VOICE_MAP:
            return VOICE_MAP[v_lower]

        parts = re.split(r"[-_]", v_lower)
        lang = parts[0] if parts and len(parts[0]) == 2 else self.default_lang
        tld = "co.in" if "in" in parts else self.default_tld
        return lang, tld

    def synthesize(self, text: str, voice: str, output_path: str) -> float:
        """Synthesize text into audio file at output_path. Return audio duration in seconds."""
        duration, _ = self.synthesize_with_timestamps(text, voice, output_path)
        return duration

    def synthesize_with_timestamps(
        self, text: str, voice: str, output_path: str
    ) -> Tuple[float, List[Dict[str, Any]]]:
        """Synthesize text into audio file and return (duration, timestamps)."""
        if not text or not text.strip():
            raise ValueError("Narration text cannot be empty.")

        cleaned_text = text.strip()
        out_path = Path(output_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        lang, tld = self._resolve_voice_params(voice)

        needs_wav_conversion = out_path.suffix.lower() == ".wav"
        temp_mp3_path: Optional[Path] = None

        if needs_wav_conversion:
            temp_mp3_path = out_path.with_name(f"{out_path.stem}_tmp_{os.getpid()}.mp3")
            target_gtts_file = temp_mp3_path
        else:
            target_gtts_file = out_path

        try:
            from gtts import gTTS

            tts = gTTS(text=cleaned_text, lang=lang, tld=tld, slow=False)
            tts.save(str(target_gtts_file))

            if not target_gtts_file.exists() or target_gtts_file.stat().st_size == 0:
                raise RuntimeError("gTTS output file was not created or is empty.")

            if needs_wav_conversion:
                ffmpeg_bin = settings.FFMPEG_PATH or shutil.which("ffmpeg")
                if not ffmpeg_bin:
                    raise RuntimeError("FFmpeg binary not available for MP3-to-WAV conversion.")

                cmd = [
                    ffmpeg_bin,
                    "-y",
                    "-i",
                    str(target_gtts_file),
                    "-c:a",
                    "pcm_s16le",
                    "-ar",
                    "24000",
                    str(out_path),
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if proc.returncode != 0:
                    raise RuntimeError(f"FFmpeg audio conversion to WAV failed: {proc.stderr[:200]}")

            if not out_path.exists() or out_path.stat().st_size == 0:
                raise RuntimeError(f"Synthesized audio file at '{out_path}' is missing or empty.")

            probe_info = probe_media(out_path)
            duration = probe_info.get("duration")
            if not duration or duration <= 0:
                if out_path.suffix.lower() == ".wav":
                    try:
                        with wave.open(str(out_path), "rb") as wf:
                            duration = round(wf.getnframes() / float(wf.getframerate()), 2)
                    except Exception:
                        duration = 5.0
                else:
                    duration = 5.0
            duration = max(1.0, round(float(duration), 2))

            sentences = [s.strip() for s in re.split(r"[.!?]+", cleaned_text) if s.strip()]
            if not sentences:
                sentences = [cleaned_text]

            word_counts = [max(1, len(s.split())) for s in sentences]
            total_words = sum(word_counts)

            timestamps: List[Dict[str, Any]] = []
            curr_time = 0.0
            for idx, (sentence, w_count) in enumerate(zip(sentences, word_counts)):
                if idx == len(sentences) - 1:
                    end_time = duration
                else:
                    prop_dur = (w_count / total_words) * duration
                    end_time = round(curr_time + prop_dur, 2)

                seg_dur = max(0.1, round(end_time - curr_time, 2))
                timestamps.append({
                    "text": sentence,
                    "start_time": round(curr_time, 2),
                    "end_time": end_time,
                    "duration": seg_dur,
                    "method": "gtts_estimated",
                    "confidence": 0.75,
                })
                curr_time = end_time

            self.last_timestamps = timestamps
            logger.info("GTTSProvider generated %.2fs audio (%s) for %d sentences", duration, out_path.name, len(sentences))
            return duration, timestamps

        except Exception as exc:
            if temp_mp3_path and temp_mp3_path.exists():
                try:
                    temp_mp3_path.unlink()
                except Exception:
                    pass
            if out_path.exists():
                try:
                    out_path.unlink()
                except Exception:
                    pass
            if isinstance(exc, ValueError):
                raise
            raise RuntimeError(f"gTTS audio generation failed: {exc}") from exc

        finally:
            if temp_mp3_path and temp_mp3_path.exists():
                try:
                    temp_mp3_path.unlink()
                except Exception:
                    pass
