"""Unit and integration tests for Phase 3B Real Stock Footage Providers."""
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.core.config import settings
from app.core.database import db
from app.providers.base import FootageCandidate
from app.providers.pexels import PexelsFootageProvider
from app.providers.pixabay import PixabayFootageProvider
from app.rendering.ffmpeg_engine import ffmpeg_engine, probe_media
from app.workers.job_runner import job_runner


MOCK_PEXELS_RESPONSE = {
    "page": 1,
    "per_page": 10,
    "total_results": 1,
    "url": "https://www.pexels.com/search/videos/nature/",
    "videos": [
        {
            "id": 998877,
            "width": 1920,
            "height": 1080,
            "duration": 12,
            "url": "https://www.pexels.com/video/998877/",
            "image": "https://images.pexels.com/videos/998877/preview.jpg",
            "user": {
                "id": 1234,
                "name": "Jane Wildlife",
                "url": "https://www.pexels.com/@jane-wildlife"
            },
            "video_files": [
                {
                    "id": 1122,
                    "quality": "hd",
                    "file_type": "video/mp4",
                    "width": 1920,
                    "height": 1080,
                    "fps": 30.0,
                    "link": "https://download.pexels.test/video_1080p.mp4"
                }
            ],
            "tags": ["nature", "trees", "calm"]
        }
    ]
}

MOCK_PIXABAY_RESPONSE = {
    "total": 1,
    "totalHits": 1,
    "hits": [
        {
            "id": 554433,
            "pageURL": "https://pixabay.com/videos/nature-stream-554433/",
            "type": "film",
            "tags": "stream, forest, water",
            "duration": 10,
            "videos": {
                "large": {
                    "url": "https://download.pixabay.test/stream_large.mp4",
                    "width": 1920,
                    "height": 1080,
                    "size": 5242880,
                    "thumbnail": "https://download.pixabay.test/thumb.jpg"
                }
            },
            "user": "NatureFilmer"
        }
    ]
}


def test_pexels_search_and_candidate_parsing():
    """1. Verify Pexels search and candidate parsing with mocked response."""
    provider = PexelsFootageProvider(api_key="mock_pexels_test_key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = MOCK_PEXELS_RESPONSE
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.Client.get", return_value=mock_resp):
        candidates = provider.search("nature trees", min_duration=5.0)

    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.clip_id == "pexels_998877"
    assert cand.provider == "pexels"
    assert cand.source_url == "https://www.pexels.com/video/998877/"
    assert cand.download_url == "https://download.pexels.test/video_1080p.mp4"
    assert cand.width == 1920
    assert cand.height == 1080
    assert cand.is_real_footage is True
    assert "Jane Wildlife" in cand.attribution
    assert "Pexels License" in cand.licence


def test_pixabay_search_and_candidate_parsing():
    """2. Verify Pixabay search and candidate parsing with mocked response."""
    provider = PixabayFootageProvider(api_key="mock_pixabay_test_key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = MOCK_PIXABAY_RESPONSE
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.Client.get", return_value=mock_resp):
        candidates = provider.search("forest stream", min_duration=5.0)

    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.clip_id == "pixabay_554433"
    assert cand.provider == "pixabay"
    assert cand.source_url == "https://pixabay.com/videos/nature-stream-554433/"
    assert cand.download_url == "https://download.pixabay.test/stream_large.mp4"
    assert cand.width == 1920
    assert cand.is_real_footage is True
    assert "NatureFilmer" in cand.attribution
    assert "Pixabay Content License" in cand.licence


def test_missing_api_keys_handled_gracefully():
    """3. Verify missing API keys return empty candidates without throwing unhandled exceptions."""
    p_unconfigured = PexelsFootageProvider(api_key="")
    assert p_unconfigured.is_configured() is False
    assert p_unconfigured.search("test query") == []

    pix_unconfigured = PixabayFootageProvider(api_key="")
    assert pix_unconfigured.is_configured() is False
    assert pix_unconfigured.search("test query") == []


def test_provider_handles_httpx_timeout_gracefully():
    """Verify timeout and transient network exceptions are caught and logged cleanly."""
    import httpx
    provider = PexelsFootageProvider(api_key="mock_key")

    with patch("httpx.Client.get", side_effect=httpx.TimeoutException("Read timed out")):
        results = provider.search("stormy clouds")
    assert results == []

    pix_provider = PixabayFootageProvider(api_key="mock_key")
    with patch("httpx.Client.get", side_effect=httpx.ConnectTimeout("Connect timed out")):
        pix_results = pix_provider.search("stormy clouds")
    assert pix_results == []


def test_invalid_json_response_handled_gracefully():
    """Verify malformed JSON responses do not cause unhandled crashes."""
    provider = PexelsFootageProvider(api_key="mock_key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.side_effect = ValueError("Invalid JSON response payload")
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.Client.get", return_value=mock_resp):
        candidates = provider.search("broken payload test")
    assert candidates == []


def test_download_clip_atomic_staging_and_cleanup(tmp_path):
    """4. Verify safe download with .part file and atomic rename."""
    provider = PexelsFootageProvider(api_key="mock_key")
    target_video = tmp_path / "downloads" / "test_clip.mp4"

    candidate = FootageCandidate(
        clip_id="test_001",
        provider="pexels",
        source_url="https://pexels.com/video/1",
        download_url="https://download.test/clip.mp4",
        licence="Pexels License",
        attribution="Creator",
        is_ai_generated=False,
        duration=5.0,
        width=1920,
        height=1080,
        fps=30.0,
        tags=["nature"],
        is_real_footage=True,
    )

    mock_stream = MagicMock()
    mock_stream.__enter__.return_value = mock_stream
    mock_stream.raise_for_status.return_value = None
    mock_stream.iter_bytes.return_value = [b"A" * 8192, b"B" * 8192]

    with patch("httpx.Client.stream", return_value=mock_stream):
        out_path = provider.download_clip(candidate, str(target_video))

    assert Path(out_path).exists()
    assert Path(out_path).stat().st_size == 16384
    # .part file must no longer exist after atomic move
    assert not target_video.with_suffix(".part").exists()


def test_failed_download_cleans_up_partial_file(tmp_path):
    """5. Verify partial download failures are cleaned up and do not leave incomplete files."""
    provider = PixabayFootageProvider(api_key="mock_key")
    target_video = tmp_path / "downloads" / "corrupted_clip.mp4"

    candidate = FootageCandidate(
        clip_id="test_fail",
        provider="pixabay",
        source_url="https://pixabay.com/1",
        download_url="https://download.test/fail.mp4",
        licence="Pixabay License",
        attribution="Creator",
        is_ai_generated=False,
        duration=5.0,
        width=1920,
        height=1080,
        fps=30.0,
        tags=["nature"],
        is_real_footage=True,
    )

    def _broken_stream(*args, **kwargs):
        mock = MagicMock()
        mock.__enter__.return_value = mock
        mock.raise_for_status.side_effect = ConnectionResetError("Connection dropped by peer")
        return mock

    with patch("httpx.Client.stream", side_effect=_broken_stream):
        with pytest.raises(Exception):
            provider.download_clip(candidate, str(target_video))

    assert not target_video.exists()
    assert not target_video.with_suffix(".part").exists()


@pytest.mark.asyncio
async def test_pexels_failure_triggers_pixabay_fallback(monkeypatch):
    """6. When primary provider (Pexels) fails or has no matches, secondary (Pixabay) is selected."""
    job_id = "test-job-pexels-to-pixabay-001"
    req_payload = {
        "script": "Rivers flowing quietly through ancient green forests.",
        "aspect_ratios": ["16:9"],
    }
    db.create_job(job_id, req_payload)

    # Broken Pexels provider
    broken_pexels = PexelsFootageProvider(api_key="dummy")
    broken_pexels.search = MagicMock(side_effect=Exception("Pexels 503 Service Unavailable"))

    # Working Pixabay provider
    working_pixabay = PixabayFootageProvider(api_key="dummy")
    pix_candidate = FootageCandidate(
        clip_id="pixabay_river_001",
        provider="pixabay",
        source_url="https://pixabay.com/videos/river-1/",
        download_url="https://pixabay.test/river.mp4",
        licence="Pixabay Content License (Free for commercial use)",
        attribution="Video by RiverShooter from Pixabay",
        is_ai_generated=False,
        duration=8.0,
        width=1920,
        height=1080,
        fps=30.0,
        tags=["river", "forest"],
        is_real_footage=True,
    )
    working_pixabay.search = MagicMock(return_value=[pix_candidate])
    working_pixabay.download_clip = MagicMock(return_value="mock_target_path")

    monkeypatch.setattr(job_runner, "primary_footage_provider", broken_pexels)
    monkeypatch.setattr(job_runner, "secondary_footage_provider", working_pixabay)

    await job_runner.execute_job(job_id)

    record = db.get_job(job_id)
    assert record["status"] == "completed"
    timeline = record["timeline"]
    assert len(timeline["scenes"]) > 0
    # The clip used should be from pixabay
    assert timeline["scenes"][0]["clip"]["provider"] == "pixabay"
    assert "RiverShooter" in timeline["scenes"][0]["clip"]["attribution"]
    assert any("used secondary footage provider fallback" in w for w in record["warnings"])


@pytest.mark.asyncio
async def test_both_providers_failing_triggers_procedural_fallback(monkeypatch):
    """7. When both stock providers fail or return nothing, pipeline falls back to procedural AI/mock."""
    job_id = "test-job-both-fail-fallback-002"
    req_payload = {
        "script": "Deep abstract thoughts on perseverance and focus.",
        "aspect_ratios": ["16:9"],
    }
    db.create_job(job_id, req_payload)

    broken_pexels = PexelsFootageProvider(api_key="dummy")
    broken_pexels.search = MagicMock(return_value=[])

    broken_pixabay = PixabayFootageProvider(api_key="dummy")
    broken_pixabay.search = MagicMock(return_value=[])

    monkeypatch.setattr(job_runner, "primary_footage_provider", broken_pexels)
    monkeypatch.setattr(job_runner, "secondary_footage_provider", broken_pixabay)

    await job_runner.execute_job(job_id)

    record = db.get_job(job_id)
    assert record["status"] == "completed"
    manifest = record["manifest"]
    assert manifest["licence_and_rights"]["contains_ai_media"] is True
    timeline = record["timeline"]
    # Fallback asset should be marked as fallback
    first_clip = timeline["scenes"][0]["clip"]
    assert first_clip["is_fallback"] is True
    assert first_clip["media_type"] == "procedural_visual"


@pytest.mark.asyncio
async def test_full_pipeline_with_real_video_clip_renders_playable_mp4s(monkeypatch, tmp_path):
    """8. End-to-end integration: Pipeline with a real downloaded stock video renders valid 16:9 & 9:16 MP4s."""
    if not ffmpeg_engine.is_available():
        pytest.skip("FFmpeg binary not available on host system")

    # Generate a genuine small 2s test video clip using FFmpeg
    sample_stock_clip = tmp_path / "sample_stock.mp4"
    gen_cmd = [
        ffmpeg_engine.ffmpeg_path,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=1280x720:rate=30",
        "-t",
        "2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(sample_stock_clip),
    ]
    subprocess.run(gen_cmd, check=True, capture_output=True)
    assert sample_stock_clip.exists()

    job_id = "test-job-real-video-render-003"
    req_payload = {
        "script": "Modern architecture rises above the urban skyline.",
        "aspect_ratios": ["16:9", "9:16"],
        "voice": "en-IN-female",
    }
    db.create_job(job_id, req_payload)

    # Set up mock provider that provides the real test video file
    real_stock_candidate = FootageCandidate(
        clip_id="pexels_sample_arch_01",
        provider="pexels",
        source_url="https://pexels.com/video/arch-1",
        download_url="https://pexels.test/arch.mp4",
        licence="Pexels License (Free for commercial use)",
        attribution="Video by CityCamera on Pexels",
        is_ai_generated=False,
        duration=2.0,
        width=1280,
        height=720,
        fps=30.0,
        tags=["architecture", "skyline"],
        is_real_footage=True,
    )

    class RealClipMockProvider(PexelsFootageProvider):
        def search(self, *args, **kwargs):
            return [real_stock_candidate]

        def download_clip(self, candidate, target_path, **kwargs):
            # Copy the genuine MP4 clip to target path
            import shutil
            shutil.copyfile(str(sample_stock_clip), target_path)
            return target_path

    monkeypatch.setattr(job_runner, "primary_footage_provider", RealClipMockProvider(api_key="test_key"))

    # Execute job
    await job_runner.execute_job(job_id)

    record = db.get_job(job_id)
    assert record["status"] == "completed"

    storage_dir = Path(settings.LOCAL_STORAGE_DIR) / job_id
    video_16_9 = storage_dir / "video_16_9.mp4"
    video_9_16 = storage_dir / "video_9_16.mp4"
    manifest_file = storage_dir / "manifest.json"

    assert video_16_9.exists()
    assert video_9_16.exists()
    assert manifest_file.exists()

    # Verify 16:9 MP4
    p16 = probe_media(video_16_9)
    assert p16["is_valid"] is True
    assert p16["has_video"] is True
    assert p16["has_audio"] is True
    assert p16["video_codec"] == "h264"
    assert p16["audio_codec"] == "aac"
    assert p16["resolution"] in ("1280x720", "1920x1080")
    assert p16["fps"] == 30.0
    assert p16["file_size_bytes"] > 5000

    # Verify 9:16 MP4 (vertical reel)
    p9 = probe_media(video_9_16)
    assert p9["is_valid"] is True
    assert p9["has_video"] is True
    assert p9["has_audio"] is True
    assert p9["video_codec"] == "h264"
    assert p9["audio_codec"] == "aac"
    assert p9["resolution"] in ("720x1280", "1080x1920")
    assert p9["fps"] == 30.0
    assert p9["file_size_bytes"] > 5000

    # Verify Manifest
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert manifest_data["licence_and_rights"]["contains_real_stock_footage"] is True
    assert manifest_data["licence_and_rights"]["commercial_use_cleared"] is True
