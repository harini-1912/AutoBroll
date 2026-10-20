import pytest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch
from app.services.transcription import AudioTranscriber, audio_transcriber
from app.workers.job_runner import job_runner
from app.core.database import db
from app.core.config import settings

def test_format_transcript_to_sentences():
    transcriber = AudioTranscriber()
    raw = "artificial intelligence is transforming how video creators work today"
    formatted = transcriber._format_transcript_to_sentences(raw)
    assert formatted.endswith(".")
    assert formatted[0].isupper()

def test_transcribe_missing_or_empty_file(tmp_path):
    transcriber = AudioTranscriber()
    assert transcriber.transcribe(None) is None
    empty_file = tmp_path / "empty.wav"
    empty_file.touch()
    assert transcriber.transcribe(empty_file) is None

@pytest.mark.asyncio
async def test_job_runner_transcribes_uploaded_audio_when_script_missing(tmp_path):
    job_id = "test-job-audio-transcribe-001"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True)
    fake_audio = job_dir / "voiceover.wav"
    
    # Write valid 2s PCM WAV
    sample_rate = 24000
    n_frames = sample_rate * 2
    with wave.open(str(fake_audio), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)

    req_payload = {
        "audio_local_path": str(fake_audio),
        "aspect_ratios": ["16:9"],
        "voice": "en-IN-female",
        "script": None,
        "scenes": None,
    }
    db.create_job(job_id, req_payload)

    with patch("app.workers.job_runner.audio_transcriber.transcribe", return_value="Espresso coffee brewing tutorial. The aroma fills the entire kitchen.") as mock_transcribe:
        await job_runner.execute_job(job_id)

    record = db.get_job(job_id)
    assert record["status"] == "completed"
    timeline = record.get("timeline") or {}
    # Verified: Script was automatically populated from the transcriber!
    assert "coffee" in timeline.get("script", "").lower()
    # Verified: Scenes were segmented from the transcribed text!
    scenes = timeline.get("scenes", [])
    assert len(scenes) >= 1
    assert any("espresso" in s.get("text", "").lower() for s in scenes)
