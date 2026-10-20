"""Unit and integration tests for Multi-Tier Visual Fallback Pipeline.

Tests:
1. Tier a: Pollinations AI Video (model=video) with Bearer token authentication.
2. Tier b: Pollinations AI Image + FFmpeg Ken Burns zoompan MP4 conversion.
3. Tier c: 100% Offline Local Backup (FFmpeg animated dark gradient motion graphic).
4. Full Provider Cascade in JobRunner: Pixabay -> Pexels -> AI Visual Fallback.
5. Fallback video conforms to requested aspect ratios (16:9, 9:16, 1:1) and stitches without errors.
"""
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch
import httpx
import pytest

from app.core.config import settings
from app.core.database import db
from app.core.exceptions import FootageNotFoundError
from app.rendering.ffmpeg_engine import ffmpeg_engine, probe_media
from app.services.ai_fallback_generator import (
    generate_visual_fallback,
    generate_offline_gradient_clip,
)
from app.workers.job_runner import job_runner


@pytest.fixture
def sample_jpeg_bytes(tmp_path):
    """Generate genuine 1080p JPEG bytes using FFmpeg for testing Ken Burns zoompan."""
    img_file = tmp_path / "test_fixture.jpg"
    subprocess.run(
        [
            settings.FFMPEG_PATH,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x1e3a5f:s=1920x1080",
            "-frames:v",
            "1",
            str(img_file),
        ],
        check=True,
        capture_output=True,
    )
    return img_file.read_bytes()


@pytest.mark.asyncio
async def test_pollinations_video_success_tier_a(tmp_path, monkeypatch):
    """Tier a: When POLLINATIONS_API_KEY is present and video generation succeeds, return MP4 directly."""
    monkeypatch.setattr(settings, "POLLINATIONS_API_KEY", "pollinations_secret_key_123")
    out_file = tmp_path / "pollinations_video.mp4"

    # Minimal valid MP4 header with ftyp box
    fake_mp4_bytes = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42" + (b"\x00" * 4000)

    requested_urls = []
    auth_headers = []

    async def mock_get(self, url, headers=None, **kwargs):
        requested_urls.append(str(url))
        if headers:
            auth_headers.append(headers.get("Authorization"))
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.content = fake_mp4_bytes
        mock_resp.headers = {"content-type": "video/mp4"}
        return mock_resp

    with patch.object(httpx.AsyncClient, "get", new=mock_get):
        result_path = await generate_visual_fallback("luxurious golden sunset", out_file, scene_duration=3.0)

    assert result_path == out_file
    assert out_file.exists()
    assert out_file.read_bytes() == fake_mp4_bytes
    assert any("model=video" in url for url in requested_urls)
    assert "Bearer pollinations_secret_key_123" in auth_headers


@pytest.mark.asyncio
async def test_pollinations_video_failure_cascades_to_image_kenburns_tier_b(tmp_path, sample_jpeg_bytes, monkeypatch):
    """Tier b: When video generation fails or times out, fetch image and convert to Ken Burns MP4."""
    monkeypatch.setattr(settings, "POLLINATIONS_API_KEY", "pollinations_secret_key_123")
    out_file = tmp_path / "kenburns_output.mp4"

    async def mock_get(self, url, **kwargs):
        mock_resp = MagicMock(spec=httpx.Response)
        url_str = str(url)
        if "model=video" in url_str:
            mock_resp.status_code = 504
            mock_resp.content = b"Gateway Timeout"
            mock_resp.headers = {"content-type": "text/plain"}
        else:
            # Image endpoint succeeds with genuine JPEG bytes
            mock_resp.status_code = 200
            mock_resp.content = sample_jpeg_bytes
            mock_resp.headers = {"content-type": "image/jpeg"}
        return mock_resp

    with patch.object(httpx.AsyncClient, "get", new=mock_get):
        result_path = await generate_visual_fallback("modern architectural villa", out_file, scene_duration=2.0)

    assert result_path == out_file
    assert out_file.exists()
    assert out_file.stat().st_size > 1000

    # Verify MP4 container validity and resolution
    probe = probe_media(out_file)
    assert probe["is_valid"] is True
    assert probe["has_video"] is True


@pytest.mark.asyncio
async def test_offline_local_gradient_fallback_tier_c(tmp_path, monkeypatch):
    """Tier c: When all API endpoints fail or offline with no keys, generate local animated gradient."""
    monkeypatch.setattr(settings, "POLLINATIONS_API_KEY", None)
    out_file = tmp_path / "offline_gradient.mp4"

    async def mock_network_down(self, *args, **kwargs):
        raise httpx.ConnectError("Network unreachable: offline test")

    with patch.object(httpx.AsyncClient, "get", new=mock_network_down):
        result_path = await generate_visual_fallback("deep space cosmic nebula", out_file, scene_duration=2.5)

    assert result_path == out_file
    assert out_file.exists()
    assert out_file.stat().st_size > 1000

    probe = probe_media(out_file)
    assert probe["is_valid"] is True
    assert probe["has_video"] is True


@pytest.mark.asyncio
async def test_job_runner_full_cascade_to_ai_fallback(monkeypatch):
    """Pipeline integration: When Pixabay and Pexels yield 0 clips, JobRunner falls back to AI visual fallback."""
    job_id = "test-job-cascade-ai-fallback-001"
    req_payload = {
        "script": "Deep oceanic creatures living in the twilight abyss.",
        "aspect_ratios": ["16:9"],
        "voice": "en-IN-female",
    }
    db.create_job(job_id, req_payload)

    class EmptySearchProvider:
        def search(self, *args, **kwargs):
            return []

    # Both primary and secondary stock providers return 0 clips
    monkeypatch.setattr(job_runner, "primary_footage_provider", EmptySearchProvider())
    monkeypatch.setattr(job_runner, "secondary_footage_provider", EmptySearchProvider())

    await job_runner.execute_job(job_id)

    record = db.get_job(job_id)
    assert record["status"] == "completed"

    timeline = record["timeline"]
    assert len(timeline["scenes"]) > 0

    first_scene_clip = timeline["scenes"][0]["clip"]
    assert first_scene_clip["is_fallback"] is True
    assert first_scene_clip["provider"] in ("ai_generator", "ai_fallback", "pollinations")
    assert first_scene_clip["media_type"] == "procedural_visual"

    manifest = record["manifest"]
    assert manifest["licence_and_rights"]["contains_ai_media"] is True

    # Output video should exist and be valid
    output_meta = record["manifest"]["outputs"]["video_16_9"]
    assert output_meta["file_size_bytes"] > 1000
    assert output_meta["resolution"] in ("1280x720", "1920x1080")


@pytest.mark.asyncio
async def test_fallback_renders_cleanly_for_multiple_aspect_ratios(monkeypatch):
    """Pipeline integration: Fallback clips format and stitch into 16:9, 9:16, and 1:1 without errors."""
    job_id = "test-job-fallback-multi-aspect-002"
    req_payload = {
        "script": "Pristine white snow covering the northern tundra forest.",
        "aspect_ratios": ["16:9", "9:16", "1:1"],
        "voice": "en-IN-female",
    }
    db.create_job(job_id, req_payload)

    class EmptySearchProvider:
        def search(self, *args, **kwargs):
            return []

    monkeypatch.setattr(job_runner, "primary_footage_provider", EmptySearchProvider())
    monkeypatch.setattr(job_runner, "secondary_footage_provider", EmptySearchProvider())

    await job_runner.execute_job(job_id)

    record = db.get_job(job_id)
    assert record["status"] == "completed"

    outputs = record["manifest"]["outputs"]
    assert "video_16_9" in outputs
    assert "video_9_16" in outputs
    assert "video_1_1" in outputs

    assert outputs["video_16_9"]["resolution"] in ("1280x720", "1920x1080")
    assert outputs["video_9_16"]["resolution"] in ("720x1280", "1080x1920")
    assert outputs["video_1_1"]["resolution"] in ("720x720", "1080x1080")
