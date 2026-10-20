"""Unit tests for Unified Video Downloader Service (app/services/video_downloader.py)."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yt_dlp
from yt_dlp.utils import DownloadError

from app.core.exceptions import InvalidInputError
from app.providers.base import FootageCandidate
from app.rendering.ffmpeg_engine import ffmpeg_engine, probe_media
from app.services.video_downloader import (
    BROWSER_HEADERS,
    BROWSER_USER_AGENT,
    MAX_VIDEO_FILESIZE_BYTES,
    _clean_error_message,
    _is_direct_video_url,
    fetch_video_from_url,
)


@pytest.fixture(scope="session")
def valid_sample_mp4(tmp_path_factory) -> Path:
    """Generate a lightweight valid MP4 clip for video testing."""
    sample_dir = tmp_path_factory.mktemp("sample_media")
    sample_file = sample_dir / "valid_sample.mp4"
    ffmpeg_engine.render_scene_clip(
        scene_id="test_sample_01",
        scene_text="Sample Test Clip",
        duration=1.0,
        aspect_ratio="16:9",
        output_path=sample_file,
        scene_index=0,
    )
    assert sample_file.exists()
    return sample_file


@pytest.mark.asyncio
async def test_direct_mp4_download_with_browser_user_agent(tmp_path, valid_sample_mp4):
    """Verify that direct MP4 URLs are downloaded with spoofed browser User-Agent headers."""
    output_path = tmp_path / "downloaded_direct.mp4"
    sample_bytes = valid_sample_mp4.read_bytes()

    # Mock httpx streaming response
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "video/mp4"}
    mock_resp.raise_for_status = MagicMock()

    async def aiter_bytes(chunk_size=65536):
        yield sample_bytes

    mock_resp.aiter_bytes = aiter_bytes

    captured_headers = {}

    class MockAsyncClient:
        def __init__(self, headers=None, **kwargs):
            nonlocal captured_headers
            captured_headers = headers or {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def stream(self, method, url):
            mock_stream_ctx = MagicMock()

            async def __aenter__(ctx_self):
                return mock_resp

            async def __aexit__(ctx_self, exc_type, exc_val, exc_tb):
                pass

            mock_stream_ctx.__aenter__ = __aenter__
            mock_stream_ctx.__aexit__ = __aexit__
            return mock_stream_ctx

    with patch("httpx.AsyncClient", side_effect=MockAsyncClient):
        result = await fetch_video_from_url("https://cdn.example.com/videos/sample_nature.mp4", output_path)

    assert result == output_path
    assert output_path.exists()
    assert output_path.stat().st_size == len(sample_bytes)
    assert captured_headers.get("User-Agent") == BROWSER_USER_AGENT

    probe = probe_media(output_path)
    assert probe["is_valid"] is True
    assert probe["has_video"] is True


@pytest.mark.asyncio
async def test_direct_video_detected_by_content_type_head(tmp_path, valid_sample_mp4):
    """Verify that URLs without .mp4 extension are detected as direct videos if Content-Type is video/*."""
    output_path = tmp_path / "stream_download.mp4"
    sample_bytes = valid_sample_mp4.read_bytes()

    mock_head_resp = MagicMock()
    mock_head_resp.status_code = 200
    mock_head_resp.headers = {"content-type": "video/mp4; charset=binary"}

    mock_stream_resp = MagicMock()
    mock_stream_resp.status_code = 200
    mock_stream_resp.headers = {"content-type": "video/mp4"}
    mock_stream_resp.raise_for_status = MagicMock()

    async def aiter_bytes(chunk_size=65536):
        yield sample_bytes

    mock_stream_resp.aiter_bytes = aiter_bytes

    class MockAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def head(self, url):
            return mock_head_resp

        def stream(self, method, url):
            mock_stream_ctx = MagicMock()

            async def __aenter__(ctx_self):
                return mock_stream_resp

            async def __aexit__(ctx_self, exc_type, exc_val, exc_tb):
                pass

            mock_stream_ctx.__aenter__ = __aenter__
            mock_stream_ctx.__aexit__ = __aexit__
            return mock_stream_ctx

    with patch("httpx.AsyncClient", side_effect=MockAsyncClient):
        result = await fetch_video_from_url("https://media.example.com/render-feed?id=99", output_path)

    assert result == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0


@pytest.mark.asyncio
async def test_webpage_url_invokes_ytdlp(tmp_path, valid_sample_mp4):
    """Verify that webpage URLs (YouTube, Vimeo, etc.) invoke yt-dlp with required options."""
    output_path = tmp_path / "ytdlp_download.mp4"
    sample_bytes = valid_sample_mp4.read_bytes()
    captured_opts = {}

    class MockYoutubeDL:
        def __init__(self, ydl_opts=None):
            nonlocal captured_opts
            captured_opts = ydl_opts or {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def download(self, urls):
            # Write out valid sample file to simulate successful download
            output_path.write_bytes(sample_bytes)

    with patch("yt_dlp.YoutubeDL", side_effect=MockYoutubeDL):
        result = await fetch_video_from_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ", output_path)

    assert result == output_path
    assert output_path.exists()
    assert captured_opts.get("outtmpl") == str(output_path)
    assert captured_opts.get("format") == "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    assert captured_opts.get("max_filesize") == 50 * 1024 * 1024
    assert captured_opts.get("quiet") is True
    assert captured_opts.get("no_warnings") is True
    assert "User-Agent" in captured_opts.get("http_headers", {})


@pytest.mark.asyncio
async def test_direct_download_failure_falls_back_to_ytdlp(tmp_path, valid_sample_mp4):
    """Verify that if direct download encounters an error, it seamlessly falls back to yt-dlp."""
    output_path = tmp_path / "fallback_ytdlp.mp4"
    sample_bytes = valid_sample_mp4.read_bytes()
    ytdlp_called = False

    class MockFailingAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def stream(self, method, url):
            raise Exception("403 Forbidden on direct fetch")

    class MockYoutubeDL:
        def __init__(self, ydl_opts=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def download(self, urls):
            nonlocal ytdlp_called
            ytdlp_called = True
            output_path.write_bytes(sample_bytes)

    with patch("httpx.AsyncClient", side_effect=MockFailingAsyncClient):
        with patch("yt_dlp.YoutubeDL", side_effect=MockYoutubeDL):
            result = await fetch_video_from_url("https://example.com/asset.mp4", output_path)

    assert ytdlp_called is True
    assert result == output_path
    assert output_path.exists()


@pytest.mark.asyncio
async def test_pixabay_cloudflare_403_falls_back_to_api(tmp_path, valid_sample_mp4):
    """Verify that when yt-dlp fails with Cloudflare 403 on Pixabay webpage, it falls back to API provider."""
    output_path = tmp_path / "pixabay_fallback.mp4"
    sample_bytes = valid_sample_mp4.read_bytes()

    class Mock403YoutubeDL:
        def __init__(self, ydl_opts=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def download(self, urls):
            raise DownloadError("ERROR: [generic] Got HTTP Error 403 caused by Cloudflare anti-bot challenge")

    mock_cand = FootageCandidate(
        clip_id="pixabay_6348",
        provider="pixabay",
        source_url="https://pixabay.com/videos/clouds-sky-timelapse-blue-sky-6348/",
        download_url="https://cdn.pixabay.com/video/2016/11/13/6348-191338843_large.mp4",
        licence="Pixabay License",
        attribution="Pixabay Creator",
        is_ai_generated=False,
        duration=10.0,
        width=1920,
        height=1080,
        fps=30.0,
        tags=["clouds"],
        is_real_footage=True,
    )

    mock_provider = MagicMock()
    mock_provider.get_by_id = MagicMock(return_value=mock_cand)

    async def mock_download_direct(url, dest):
        dest.write_bytes(sample_bytes)
        return dest

    with patch("yt_dlp.YoutubeDL", side_effect=Mock403YoutubeDL):
        with patch("app.providers.pixabay.PixabayFootageProvider", return_value=mock_provider):
            with patch("app.services.video_downloader._download_direct_stream", side_effect=mock_download_direct):
                result = await fetch_video_from_url(
                    "https://pixabay.com/videos/clouds-sky-timelapse-blue-sky-6348/",
                    output_path,
                )

    assert result == output_path
    assert output_path.exists()
    mock_provider.get_by_id.assert_called_once_with("6348")


@pytest.mark.asyncio
async def test_pexels_cloudflare_403_falls_back_to_api(tmp_path, valid_sample_mp4):
    """Verify that when yt-dlp fails with Cloudflare 403 on Pexels webpage, it falls back to API provider."""
    output_path = tmp_path / "pexels_fallback.mp4"
    sample_bytes = valid_sample_mp4.read_bytes()

    class Mock403YoutubeDL:
        def __init__(self, ydl_opts=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def download(self, urls):
            raise DownloadError("ERROR: [generic] Got HTTP Error 403: Forbidden")

    mock_cand = FootageCandidate(
        clip_id="pexels_1409899",
        provider="pexels",
        source_url="https://www.pexels.com/video/waves-crashing-1409899/",
        download_url="https://videos.pexels.com/video-files/1409899/1409899-hd_1920_1080_25fps.mp4",
        licence="Pexels License",
        attribution="Pexels Creator",
        is_ai_generated=False,
        duration=12.0,
        width=1920,
        height=1080,
        fps=25.0,
        tags=["waves"],
        is_real_footage=True,
    )

    mock_provider = MagicMock()
    mock_provider.get_by_id = MagicMock(return_value=mock_cand)

    async def mock_download_direct(url, dest):
        dest.write_bytes(sample_bytes)
        return dest

    with patch("yt_dlp.YoutubeDL", side_effect=Mock403YoutubeDL):
        with patch("app.providers.pexels.PexelsFootageProvider", return_value=mock_provider):
            with patch("app.services.video_downloader._download_direct_stream", side_effect=mock_download_direct):
                result = await fetch_video_from_url(
                    "https://www.pexels.com/video/waves-crashing-1409899/",
                    output_path,
                )

    assert result == output_path
    assert output_path.exists()
    mock_provider.get_by_id.assert_called_once_with("1409899")


@pytest.mark.asyncio
async def test_invalid_url_schemes_rejected(tmp_path):
    """Verify that disallowed protocols and empty URLs raise InvalidInputError."""
    out = tmp_path / "out.mp4"

    with pytest.raises(InvalidInputError, match="must not be empty"):
        await fetch_video_from_url("", out)

    with pytest.raises(InvalidInputError, match="Disallowed characters or protocol"):
        await fetch_video_from_url("file:///etc/passwd", out)

    with pytest.raises(InvalidInputError, match="Disallowed characters or protocol"):
        await fetch_video_from_url("https://example.com/../sneaky", out)

    with pytest.raises(InvalidInputError, match="Unsupported URL scheme"):
        await fetch_video_from_url("gopher://server.com/video.mp4", out)


@pytest.mark.asyncio
async def test_corrupted_file_fails_probe_validation(tmp_path):
    """Verify that if downloaded file is corrupted or not a valid video, probe validation raises InvalidInputError."""
    out = tmp_path / "corrupt.mp4"

    # Simulate downloading corrupted non-video bytes
    async def mock_download_corrupt(url, dest):
        dest.write_bytes(b"NOT_A_VALID_VIDEO_PAYLOAD_AT_ALL_CORRUPT")
        return dest

    with patch("app.services.video_downloader._download_direct_stream", side_effect=mock_download_corrupt):
        with pytest.raises(InvalidInputError, match="Video validation failed"):
            await fetch_video_from_url("https://cdn.example.com/corrupt.mp4", out)

    assert not out.exists()


@pytest.mark.asyncio
async def test_filesize_limit_enforced(tmp_path):
    """Verify that files exceeding the 50MB safety limit are rejected."""
    out = tmp_path / "oversized.mp4"

    # Mock chunk generator yielding chunks exceeding 50MB
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "video/mp4"}
    mock_resp.raise_for_status = MagicMock()

    async def aiter_huge_chunks(chunk_size=65536):
        # 51 MB in 1MB chunks
        chunk = b"X" * (1024 * 1024)
        for _ in range(51):
            yield chunk

    mock_resp.aiter_bytes = aiter_huge_chunks

    class MockAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def stream(self, method, url):
            mock_stream_ctx = MagicMock()

            async def __aenter__(ctx_self):
                return mock_resp

            async def __aexit__(ctx_self, exc_type, exc_val, exc_tb):
                pass

            mock_stream_ctx.__aenter__ = __aenter__
            mock_stream_ctx.__aexit__ = __aexit__
            return mock_stream_ctx

    with patch("httpx.AsyncClient", side_effect=MockAsyncClient):
        with pytest.raises(InvalidInputError, match="Video exceeds maximum allowed size of 50MB"):
            await fetch_video_from_url("https://cdn.example.com/large.mp4", out)


def test_clean_error_message():
    """Verify that ANSI escape codes and noisy traceback lines are cleanly parsed."""
    raw_err = "\x1b[0;31mERROR:\x1b[0m [generic] Got HTTP Error 403: Forbidden\nTraceback (most recent call last):\n..."
    cleaned = _clean_error_message(raw_err)
    assert cleaned == "[generic] Got HTTP Error 403: Forbidden"


@pytest.mark.asyncio
async def test_scene_swapper_with_custom_video_url(tmp_path, valid_sample_mp4):
    """Verify that SceneSwapper routes custom video_url through fetch_video_from_url and updates metadata."""
    from app.core.database import db
    from app.services.scene_swapper import scene_swapper
    from app.schemas.job import SceneClipReplacementRequest
    from app.core.config import settings

    job_id = "test_swap_job_001"
    scene_id = "scene_001"
    job_dir = Path(settings.LOCAL_STORAGE_DIR) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Setup scenes directories and mock clip
    for r in ["16_9", "9_16"]:
        scenes_dir = job_dir / f"scenes_{r}"
        scenes_dir.mkdir(parents=True, exist_ok=True)
        clip_file = scenes_dir / "clip_000.mp4"
        clip_file.write_bytes(valid_sample_mp4.read_bytes())

    timeline = {
        "job_id": job_id,
        "total_duration": 1.0,
        "scenes": [
            {
                "scene_id": scene_id,
                "text": "Initial scene narration",
                "duration": 1.0,
                "clip": {
                    "clip_id": "initial_clip",
                    "provider": "mock",
                }
            }
        ]
    }
    manifest = {
        "job_id": job_id,
        "warnings": [],
    }

    db.create_job(
        job_id=job_id,
        request_data={"aspect_ratios": ["16:9", "9:16"]},
        idempotency_key="swap_test_key_001",
    )
    db.complete_job_success(
        job_id=job_id,
        result_urls={},
        timeline=timeline,
        manifest=manifest,
        warnings=[],
    )

    swap_req = SceneClipReplacementRequest(
        video_url="https://cdn.example.com/custom_nature_footage.mp4"
    )

    # Mock fetch_video_from_url to return valid_sample_mp4
    async def mock_fetch(url, out_path):
        out_path.write_bytes(valid_sample_mp4.read_bytes())
        return out_path

    with patch("app.services.scene_swapper.fetch_video_from_url", side_effect=mock_fetch) as mock_fetch_fn:
        result = await scene_swapper.replace_scene_clip(job_id, scene_id, swap_req)

    assert mock_fetch_fn.called
    assert result["status"] == "completed"
    assert result["swapped_clip"]["provider"] == "custom_url"
    assert result["swapped_clip"]["source_url"] == "https://cdn.example.com/custom_nature_footage.mp4"
    assert result["swapped_clip"]["is_real_stock_footage"] is True


@pytest.mark.asyncio
async def test_scene_swapper_with_unparseable_video_url_returns_clean_error():
    """Verify that an unparseable video URL raises clean InvalidInputError without unhandled crash."""
    from app.core.database import db
    from app.core.config import settings
    from app.services.scene_swapper import scene_swapper
    from app.schemas.job import SceneClipReplacementRequest

    job_id = "test_swap_job_err"
    scene_id = "scene_001"
    job_dir = Path(settings.LOCAL_STORAGE_DIR) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    timeline = {
        "job_id": job_id,
        "total_duration": 1.0,
        "scenes": [
            {
                "scene_id": scene_id,
                "text": "Initial scene narration",
                "duration": 1.0,
                "clip": {"clip_id": "initial_clip"}
            }
        ]
    }
    db.create_job(
        job_id=job_id,
        request_data={"aspect_ratios": ["16:9"]},
        idempotency_key="swap_test_key_err",
    )
    db.complete_job_success(
        job_id=job_id,
        result_urls={},
        timeline=timeline,
        manifest={},
        warnings=[],
    )

    swap_req = SceneClipReplacementRequest(
        video_url="https://invalid-nonexistent-domain.xyz/video.mp4"
    )

    with patch(
        "app.services.scene_swapper.fetch_video_from_url",
        side_effect=InvalidInputError("Could not download video: Connection failed")
    ):
        with pytest.raises(InvalidInputError, match="Could not download video: Connection failed"):
            await scene_swapper.replace_scene_clip(job_id, scene_id, swap_req)
