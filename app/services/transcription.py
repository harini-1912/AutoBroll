"""Audio Transcription Service using FFmpeg normalization and Google Speech Recognition."""
import logging
import os
import re
import subprocess
import wave
from pathlib import Path
from typing import Optional

logger = logging.getLogger("incubrix.transcription")


class AudioTranscriber:
    """Transcribes spoken audio into text using Google Speech Recognition."""

    def __init__(self, ffmpeg_path: Optional[str] = None):
        self.ffmpeg_path = ffmpeg_path

    def _transcribe_whisper(self, audio_path: Path) -> Optional[str]:
        """Transcribe audio using Cloud Whisper API (cloud-safe, fast, and never blocked by datacenter IP filters)."""
        import os
        import httpx
        from app.core.config import settings

        api_key = getattr(settings, "POLLINATIONS_API_KEY", "") or os.getenv(
            "POLLINATIONS_API_KEY", "sk_GAvJaRf8drlVtRQwBNdHUKWYMdgIFf70"
        )
        if not api_key:
            return None

        url = "https://gen.pollinations.ai/v1/audio/transcriptions"
        ext = audio_path.suffix.lower()
        content_type = "audio/mpeg" if ext == ".mp3" else ("audio/wav" if ext == ".wav" else "audio/mp4")

        try:
            with open(audio_path, "rb") as f:
                files = {"file": (audio_path.name, f, content_type)}
                data = {"model": "whisper-1"}
                headers = {"Authorization": f"Bearer {api_key}"}
                with httpx.Client(timeout=45.0) as client:
                    resp = client.post(url, headers=headers, files=files, data=data)
                    if resp.status_code == 200:
                        res_json = resp.json()
                        text = (res_json.get("text") or "").strip()
                        if text:
                            logger.info("Successfully transcribed audio via Cloud Whisper: '%s'", text[:100])
                            return text
                    else:
                        logger.warning(
                            "Cloud Whisper returned HTTP %s: %s; falling back to local STT",
                            resp.status_code,
                            resp.text[:150],
                        )
        except Exception as e:
            logger.warning("Cloud Whisper request failed: %s; falling back to local STT", e)

        return None

    def transcribe(self, audio_path: Path, max_chunk_duration: float = 12.0) -> Optional[str]:
        """Transcribe spoken voiceover audio into clean text.
        
        1. First tries Cloud Whisper API (fast, punctuation-accurate, works in cloud containers like Render).
        2. Falls back to local FFmpeg normalization + Google Speech Recognition if Cloud STT is unavailable.
        3. Formats sentences with clean capitalization and punctuation.
        """
        if not audio_path or not Path(audio_path).exists() or Path(audio_path).stat().st_size == 0:
            return None

        # Tier 1: Cloud Whisper API
        whisper_text = self._transcribe_whisper(audio_path)
        if whisper_text and len(whisper_text.strip()) > 3:
            return self._format_segments_to_sentences([whisper_text])

        # Tier 2: Local Google Speech Recognition with loudness normalization
        import speech_recognition as sr
        from app.rendering.ffmpeg_engine import probe_media, ffmpeg_engine

        eff_ffmpeg = self.ffmpeg_path or ffmpeg_engine.ffmpeg_path

        # 1. Convert to a temporary 16kHz mono PCM WAV with audio loudness normalization
        tmp_wav = audio_path.parent / f"transcribe_{audio_path.stem}.wav"
        cmd = [
            eff_ffmpeg,
            "-y",
            "-i", str(audio_path),
            "-ac", "1",
            "-ar", "16000",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,volume=1.2",
            "-c:a", "pcm_s16le",
            str(tmp_wav)
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
            target_wav = tmp_wav if (res.returncode == 0 and tmp_wav.exists() and tmp_wav.stat().st_size > 1000) else None
        except Exception as e:
            logger.warning("FFmpeg loudnorm conversion error: %s; trying simple conversion", e)
            target_wav = None

        if not target_wav:
            # Fallback simple conversion without loudnorm filter
            cmd_simple = [
                eff_ffmpeg,
                "-y",
                "-i", str(audio_path),
                "-ac", "1",
                "-ar", "16000",
                "-c:a", "pcm_s16le",
                str(tmp_wav)
            ]
            try:
                res_s = subprocess.run(cmd_simple, capture_output=True, text=True, timeout=30)
                if res_s.returncode == 0 and tmp_wav.exists() and tmp_wav.stat().st_size > 1000:
                    target_wav = tmp_wav
            except Exception as e:
                logger.warning("FFmpeg simple conversion error: %s", e)

        if not target_wav or not target_wav.exists():
            target_wav = audio_path

        # 2. Determine exact duration
        total_duration = 0.0
        try:
            with wave.open(str(target_wav), "rb") as wf:
                total_duration = wf.getnframes() / float(wf.getframerate())
        except Exception:
            p_info = probe_media(audio_path)
            total_duration = float(p_info.get("duration", 0.0))

        if total_duration <= 0.0:
            total_duration = 30.0

        r = sr.Recognizer()
        r.energy_threshold = 40
        r.dynamic_energy_threshold = False
        r.pause_threshold = 0.6

        transcribed_segments = []

        try:
            with sr.AudioFile(str(target_wav)) as source:
                chunk_len = max(5.0, min(12.0, max_chunk_duration))
                offset = 0.0

                while offset < total_duration:
                    cdur = min(chunk_len, total_duration - offset)
                    try:
                        audio_data = r.record(source, duration=cdur)
                    except Exception as rec_err:
                        logger.debug("r.record exception at offset %.2f: %s", offset, rec_err)
                        break

                    # Multi-language recognition attempt for high accuracy
                    chunk_text = None
                    for lang in ["en-US", "en-IN", "en-GB"]:
                        try:
                            t = r.recognize_google(audio_data, language=lang)
                            if t and t.strip():
                                chunk_text = t.strip()
                                break
                        except sr.UnknownValueError:
                            continue
                        except Exception as e:
                            logger.debug("Google STT error for %s at %.2f: %s", lang, offset, e)
                            break

                    if chunk_text:
                        transcribed_segments.append(chunk_text)

                    offset += cdur

        except Exception as exc:
            logger.warning("Failed processing audio with SpeechRecognition: %s", exc)
        finally:
            if tmp_wav.exists() and tmp_wav != audio_path:
                try:
                    tmp_wav.unlink(missing_ok=True)
                except Exception:
                    pass

        if not transcribed_segments:
            return None

        formatted_script = self._format_segments_to_sentences(transcribed_segments)
        return formatted_script

    @staticmethod
    def _format_segments_to_sentences(segments: list) -> str:
        """Format speech segments into clean, punctuated sentences."""
        clean_sentences = []
        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue
            if any(p in seg for p in [".", "!", "?"]):
                parts = [s.strip().capitalize() for s in re.split(r"[.!?]+", seg) if s.strip()]
                clean_sentences.append(". ".join(parts) + ".")
            else:
                words = seg.split()
                if len(words) > 16:
                    for i in range(0, len(words), 12):
                        chunk = words[i:i + 12]
                        clean_sentences.append(" ".join(chunk).capitalize() + ".")
                else:
                    clean_sentences.append(seg.capitalize() + ".")

        return " ".join(clean_sentences)


audio_transcriber = AudioTranscriber()
