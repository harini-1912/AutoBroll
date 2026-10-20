import logging
import re
from pathlib import Path
from typing import Optional
import subprocess

logger = logging.getLogger("incubrix.transcription")

class AudioTranscriber:
    """Transcribes spoken audio into text using Google Speech Recognition."""

    def __init__(self, ffmpeg_path: Optional[str] = None):
        self.ffmpeg_path = ffmpeg_path

    def transcribe(self, audio_path: Path, max_chunk_duration: float = 45.0) -> Optional[str]:
        if not audio_path or not Path(audio_path).exists() or Path(audio_path).stat().st_size == 0:
            return None

        import speech_recognition as sr
        from app.rendering.ffmpeg_engine import probe_media, ffmpeg_engine

        eff_ffmpeg = self.ffmpeg_path or ffmpeg_engine.ffmpeg_path
        p_info = probe_media(audio_path)
        total_duration = float(p_info.get("duration", 0.0))

        # Convert to a temporary 16kHz mono PCM WAV for maximum recognition compatibility
        tmp_wav = audio_path.parent / f"transcribe_{audio_path.stem}.wav"
        cmd = [
            eff_ffmpeg,
            "-y",
            "-i", str(audio_path),
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "pcm_s16le",
            str(tmp_wav)
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            target_wav = tmp_wav if (res.returncode == 0 and tmp_wav.exists()) else audio_path
        except Exception as e:
            logger.warning("FFmpeg pre-transcription conversion error: %s", e)
            target_wav = audio_path

        r = sr.Recognizer()
        transcribed_segments = []

        try:
            with sr.AudioFile(str(target_wav)) as source:
                if total_duration <= max_chunk_duration:
                    audio_data = r.record(source)
                    try:
                        text = r.recognize_google(audio_data)
                        if text:
                            transcribed_segments.append(text.strip())
                    except sr.UnknownValueError:
                        logger.warning("Google Speech Recognition could not understand audio.")
                    except Exception as e:
                        logger.warning("Google Speech Recognition error: %s", e)
                else:
                    # Audio exceeds single chunk limit, transcribe in segments
                    offset = 0.0
                    while offset < total_duration:
                        chunk_dur = min(max_chunk_duration, total_duration - offset)
                        audio_data = r.record(source, duration=chunk_dur)
                        try:
                            chunk_text = r.recognize_google(audio_data)
                            if chunk_text:
                                transcribed_segments.append(chunk_text.strip())
                        except Exception:
                            pass
                        offset += chunk_dur
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

        raw_transcript = " ".join(transcribed_segments).strip()
        if not raw_transcript:
            return None

        # Format transcript into punctuated sentences if missing
        # Google STT returns unpunctuated lowercase streams
        formatted_script = self._format_transcript_to_sentences(raw_transcript)
        return formatted_script

    @staticmethod
    def _format_transcript_to_sentences(text: str) -> str:
        words = text.split()
        if not words:
            return text

        # If already has sentence punctuation, preserve it
        if any(p in text for p in [".", "!", "?"]):
            return ". ".join([s.strip().capitalize() for s in re.split(r"[.!?]+", text) if s.strip()]) + "."

        # Otherwise, break into natural sentences of 7-12 words
        sentences = []
        chunk_size = 9
        for i in range(0, len(words), chunk_size):
            chunk = words[i:i + chunk_size]
            s = " ".join(chunk).capitalize()
            sentences.append(s + ".")

        return " ".join(sentences)

audio_transcriber = AudioTranscriber()
