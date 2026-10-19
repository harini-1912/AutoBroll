"""Automated Unit & Regression Tests for Phase 4A TTS Integration."""
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List
import pytest

from app.core.config import settings
from app.core.database import db
from app.core.exceptions import TTSGenerationError
from app.providers import get_tts_provider
from app.providers.gtts_provider import GTTSProvider, VOICE_MAP
from app.providers.mock import MockTTSProvider
from app.rendering.ffmpeg_engine import probe_media
from app.workers.job_runner import JobRunner


def _create_mock_mp3(destination_path: Path, duration: float = 2.0) -> None:
    """Deterministic local audio file generator mimicking gTTS mp3 output without network."""
    ffmpeg_bin = settings.FFMPEG_PATH or shutil.which("ffmpeg")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration}",
        "-c:a",
        "libmp3lame",
        str(destination_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)


class FakeGTTS:
    """Mock for gtts.gTTS to test without real network."""
    def __init__(self, text: str, lang: str = "en", tld: str = "com", slow: bool = False):
        self.text = text
        self.lang = lang
        self.tld = tld
        self.slow = slow

    def save(self, filepath: str) -> None:
        _create_mock_mp3(Path(filepath), duration=2.5)


class FailingFakeGTTS:
    """Mock for gtts.gTTS simulating network failure."""
    def __init__(self, *args, **kwargs):
        pass

    def save(self, filepath: str) -> None:
        raise ConnectionError("Network unreachable: failed to connect to Google TTS endpoint.")


# ---------------------------------------------------------------------------
# Task 4.1: Real provider selection when configured
# ---------------------------------------------------------------------------
def test_real_provider_selection_when_configured(monkeypatch):
    """Verify that TTS_PROVIDER='gtts' instantiates GTTSProvider."""
    monkeypatch.setattr(settings, "TTS_PROVIDER", "gtts")

    provider = get_tts_provider()
    assert isinstance(provider, GTTSProvider)

    # Alias 'google' should also map to GTTSProvider
    alias_provider = get_tts_provider("google")
    assert isinstance(alias_provider, GTTSProvider)

    # JobRunner should reflect GTTSProvider
    runner = JobRunner()
    assert isinstance(runner.tts_provider, GTTSProvider)


# ---------------------------------------------------------------------------
# Task 4.2: Mock provider selection when configured
# ---------------------------------------------------------------------------
def test_mock_provider_selection_when_configured(monkeypatch):
    """Verify that TTS_PROVIDER='mock' instantiates MockTTSProvider."""
    monkeypatch.setattr(settings, "TTS_PROVIDER", "mock")

    provider = get_tts_provider()
    assert isinstance(provider, MockTTSProvider)

    # Alias 'test' should also map to MockTTSProvider
    test_provider = get_tts_provider("test")
    assert isinstance(test_provider, MockTTSProvider)

    runner = JobRunner()
    assert isinstance(runner.tts_provider, MockTTSProvider)


# ---------------------------------------------------------------------------
# Task 4.3: Invalid or unsupported provider configuration
# ---------------------------------------------------------------------------
def test_invalid_or_unsupported_provider_configuration(monkeypatch):
    """Verify unsupported or empty TTS_PROVIDER raises ValueError and does not silently fall back."""
    monkeypatch.setattr(settings, "TTS_PROVIDER", "elevenlabs_unsupported")

    with pytest.raises(ValueError, match="Unsupported TTS_PROVIDER: 'elevenlabs_unsupported'"):
        get_tts_provider()

    with pytest.raises(ValueError, match="Unsupported TTS_PROVIDER"):
        runner = JobRunner()
        _ = runner.tts_provider

    # Empty or whitespace provider
    with pytest.raises(ValueError, match="TTS_PROVIDER is not configured or is empty"):
        get_tts_provider("")

    with pytest.raises(ValueError, match="TTS_PROVIDER is not configured or is empty"):
        get_tts_provider("   ")


# ---------------------------------------------------------------------------
# Task 4.4: Real provider failure propagation & cleanup
# ---------------------------------------------------------------------------
def test_real_provider_failure_propagation(monkeypatch, tmp_path):
    """Verify network failures propagate useful errors and clean up corrupt/partial files."""
    monkeypatch.setattr("gtts.gTTS", FailingFakeGTTS)

    provider = GTTSProvider()
    target_path = tmp_path / "failed_narration.wav"

    with pytest.raises(RuntimeError) as exc_info:
        provider.synthesize("Narration script line.", "en-IN-female", str(target_path))

    assert "gTTS audio generation failed" in str(exc_info.value)
    assert "Network unreachable" in str(exc_info.value)

    # Ensure no partial/corrupt file was left behind
    assert not target_path.exists()


# ---------------------------------------------------------------------------
# Task 4.5: Empty narration text validation
# ---------------------------------------------------------------------------
def test_empty_narration_text(tmp_path):
    """Verify empty or blank narration text raises ValueError and creates no files."""
    provider = GTTSProvider()
    target_path = tmp_path / "empty.wav"

    with pytest.raises(ValueError, match="Narration text cannot be empty"):
        provider.synthesize("", "en-IN-female", str(target_path))

    with pytest.raises(ValueError, match="Narration text cannot be empty"):
        provider.synthesize("   \n\t  ", "en-IN-female", str(target_path))

    with pytest.raises(ValueError, match="Narration text cannot be empty"):
        provider.synthesize_with_timestamps(None, "en-IN-female", str(target_path))

    assert not target_path.exists()


# ---------------------------------------------------------------------------
# Task 4.6: Successful audio output validation
# ---------------------------------------------------------------------------
def test_successful_audio_output_validation(monkeypatch, tmp_path):
    """Verify GTTSProvider creates a playable audio file with estimated timestamps."""
    monkeypatch.setattr("gtts.gTTS", FakeGTTS)

    provider = GTTSProvider()
    out_wav = tmp_path / "subdir" / "narration.wav"

    duration, timestamps = provider.synthesize_with_timestamps(
        text="First sentence for testing. Second sentence has more words here.",
        voice="en-IN-female",
        output_path=str(out_wav),
    )

    # Audio file assertions
    assert out_wav.exists()
    assert out_wav.stat().st_size > 0
    assert duration > 0.0

    probe = probe_media(out_wav)
    assert probe["has_audio"] is True
    assert probe["audio_codec"] == "pcm_s16le"

    # Timestamp assertions
    assert len(timestamps) == 2
    assert timestamps[0]["text"] == "First sentence for testing"
    assert timestamps[0]["method"] == "gtts_estimated"
    assert timestamps[1]["text"] == "Second sentence has more words here"
    assert timestamps[0]["start_time"] == 0.0
    assert timestamps[1]["end_time"] == pytest.approx(duration, abs=0.1)

    # Verify no temporary mp3 was leaked in directory
    files_in_dir = list((tmp_path / "subdir").iterdir())
    assert len(files_in_dir) == 1
    assert files_in_dir[0].name == "narration.wav"


# ---------------------------------------------------------------------------
# Task 4.7: Voice parameter mapping
# ---------------------------------------------------------------------------
def test_voice_parameter_mapping():
    """Verify voice identifier string correctly maps to appropriate lang and tld."""
    provider = GTTSProvider()

    assert provider._resolve_voice_params("en-IN-female") == ("en", "co.in")
    assert provider._resolve_voice_params("en-US-male") == ("en", "com")
    assert provider._resolve_voice_params("en-GB-female") == ("en", "co.uk")
    assert provider._resolve_voice_params("hi-IN-female") == ("hi", "co.in")
    assert provider._resolve_voice_params("ta-IN-male") == ("ta", "co.in")

    # Custom or fallback
    assert provider._resolve_voice_params("fr-FR") == ("fr", "co.in")
    assert provider._resolve_voice_params(None) == ("en", "co.in")


# ---------------------------------------------------------------------------
# Task 4.8: Pipeline integration with real TTS provider (mocked network)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_job_pipeline_with_real_tts_provider(monkeypatch, tmp_path):
    """Test full JobRunner pipeline execution with TTS_PROVIDER='gtts'."""
    monkeypatch.setattr(settings, "TTS_PROVIDER", "gtts")
    monkeypatch.setattr("gtts.gTTS", FakeGTTS)

    runner = JobRunner()
    assert isinstance(runner.tts_provider, GTTSProvider)

    # Insert a job
    job_id = "test_job_real_tts_001"
    req_payload = {
        "script": "Building sustainable products with artificial intelligence. High quality narration.",
        "voice": "en-IN-female",
        "aspect_ratios": ["16:9"],
        "style": "cinematic tech",
        "avoid": [],
    }
    db.create_job(job_id, req_payload)

    await runner.execute_job(job_id)

    job = db.get_job(job_id)
    assert job["status"] == "completed"
    assert job["progress"] == 100
    assert job["stage"] == "completed"
    assert job["error_payload"] is None

    # Verify timeline and outputs
    timeline = db.get_timeline(job_id)
    assert timeline is not None
    assert timeline["total_duration"] > 0
    assert len(timeline["scenes"]) > 0

    manifest = db.get_manifest(job_id)
    assert manifest is not None
    assert "video_16_9" in manifest["outputs"]


# ---------------------------------------------------------------------------
# Task 4.9: Pipeline failure when TTS provider fails
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_job_pipeline_fails_gracefully_on_tts_failure(monkeypatch):
    """Verify JobRunner marks job failed with TTS_GENERATION_FAILED when provider throws."""
    monkeypatch.setattr(settings, "TTS_PROVIDER", "gtts")
    monkeypatch.setattr("gtts.gTTS", FailingFakeGTTS)

    runner = JobRunner()
    job_id = "test_job_tts_fail_002"
    req_payload = {
        "script": "Narration will fail due to network outage.",
        "voice": "en-IN-female",
        "aspect_ratios": ["16:9"],
        "style": "cinematic tech",
        "avoid": [],
    }
    db.create_job(job_id, req_payload)

    await runner.execute_job(job_id)

    job = db.get_job(job_id)
    assert job["status"] == "failed"
    assert job["error_payload"] is not None
    assert job["error_payload"]["code"] == "TTS_GENERATION_FAILED"
    assert "Network unreachable" in job["error_payload"]["message"]
