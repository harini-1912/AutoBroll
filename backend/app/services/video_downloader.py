"""Unified Video Downloader Service.

Handles downloading arbitrary video URLs:
- Direct video files (.mp4, .mov, .webm, or Content-Type: video/*) using standard browser User-Agent headers.
- Webpage links (YouTube, Vimeo, Pixabay, Pexels, social media, etc.) programmatically via yt-dlp.
- Resilient fallback for stock sites (Pixabay, Pexels) if anti-bot protections intercept requests.
- Validates the resulting media file with FFmpeg/FFprobe before handing it off to the video pipeline.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError

from app.core.config import settings
from app.core.exceptions import InvalidInputError
from app.core.logging import logger
from app.rendering.ffmpeg_engine import ffmpeg_engine, probe_media

# Spoofed modern desktop browser headers to prevent anti-bot 403 Forbidden
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

BROWSER_HEADERS = {
    "User-Agent": BROWSER_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,video/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Mode": "navigate",
}

DIRECT_VIDEO_EXTENSIONS = (
    ".mp4",
    ".mov",
    ".webm",
    ".mkv",
    ".m4v",
    ".avi",
    ".ts",
)

MAX_VIDEO_FILESIZE_BYTES = 50 * 1024 * 1024  # 50MB safety limit


def _clean_error_message(err_str: str) -> str:
    """Strip ANSI codes and format yt-dlp or network error output into a clean string."""
    clean = re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", err_str)
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    if not lines:
        return "Unknown error while processing video URL"
    for line in lines:
        if line.startswith("ERROR:"):
            return line.replace("ERROR:", "").strip()
    return lines[-1]


async def _is_direct_video_url(url: str, parsed_url: urllib.parse.ParseResult) -> bool:
    """Determine whether the URL points directly to a raw video stream or media file."""
    path_lower = parsed_url.path.lower()
    if any(path_lower.endswith(ext) for ext in DIRECT_VIDEO_EXTENSIONS):
        return True

    # Check Content-Type via HEAD request with spoofed browser headers
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers=BROWSER_HEADERS) as client:
            head_resp = await client.head(url)
            if head_resp.status_code < 400:
                ct = (head_resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                if ct.startswith("video/"):
                    return True
    except Exception:
        pass

    return False


async def _download_direct_stream(url: str, output_path: Path) -> Path:
    """Download a direct raw video file with browser User-Agent spoofing and size validation."""
    temp_target = output_path.with_suffix(output_path.suffix + ".part")
    total_bytes = 0

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=BROWSER_HEADERS) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                ct = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                if ct.startswith("text/html"):
                    raise InvalidInputError(f"Direct video URL returned HTML webpage instead of video ({ct}).")

                with open(temp_target, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        total_bytes += len(chunk)
                        if total_bytes > MAX_VIDEO_FILESIZE_BYTES:
                            raise InvalidInputError(
                                f"Video exceeds maximum allowed size of {MAX_VIDEO_FILESIZE_BYTES // (1024 * 1024)}MB."
                            )
                        f.write(chunk)

        if total_bytes == 0:
            raise InvalidInputError("Downloaded video is empty (0 bytes).")

        if output_path.exists():
            output_path.unlink()
        shutil.move(str(temp_target), str(output_path))
        return output_path

    finally:
        if temp_target.exists():
            try:
                temp_target.unlink()
            except Exception:
                pass


def _execute_ytdlp(url: str, output_path: Path) -> Path:
    """Synchronously invoke yt-dlp to download media from arbitrary webpages and video sites."""
    ydl_opts: Dict[str, Any] = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": str(output_path),
        "quiet": True,
        "no_warnings": True,
        "max_filesize": MAX_VIDEO_FILESIZE_BYTES,
        "http_headers": BROWSER_HEADERS,
    }

    if ffmpeg_engine.is_available():
        ydl_opts["ffmpeg_location"] = ffmpeg_engine.ffmpeg_path

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except (DownloadError, ExtractorError) as e:
        clean_msg = _clean_error_message(str(e))
        raise InvalidInputError(f"yt-dlp failed to download video: {clean_msg}")
    except Exception as e:
        clean_msg = _clean_error_message(str(e))
        raise InvalidInputError(f"Failed to download video stream: {clean_msg}")

    # Verify that the expected output file exists
    if output_path.exists() and output_path.stat().st_size > 0:
        return output_path

    # Check if yt-dlp appended or modified the extension (e.g. .mkv or .webm)
    parent = output_path.parent
    stem = output_path.stem
    candidates = [
        f for f in parent.glob(f"{stem}*")
        if f.is_file() and f.stat().st_size > 0 and not f.name.endswith(".part")
    ]
    if candidates:
        chosen = candidates[0]
        if chosen != output_path:
            if output_path.exists():
                output_path.unlink()
            shutil.move(str(chosen), str(output_path))
        return output_path

    raise InvalidInputError(f"Could not locate downloaded video file from '{url}'.")


async def _resolve_stock_webpage_fallback(url: str, parsed_url: urllib.parse.ParseResult, output_path: Path) -> Optional[Path]:
    """Fallback handler for stock site webpages (Pixabay, Pexels) where Cloudflare anti-bot blocks generic scrapers."""
    domain = parsed_url.netloc.lower()
    path = parsed_url.path

    # Check for Pixabay video webpage
    if "pixabay.com" in domain:
        match = re.search(r"/videos/(?:.*[-/])?(\d+)/?$", path)
        if match:
            media_id = match.group(1)
            logger.info("Resolving Pixabay page fallback via API for media ID: %s", media_id)
            from app.providers.pixabay import PixabayFootageProvider
            provider = PixabayFootageProvider()
            cand = await asyncio.to_thread(provider.get_by_id, media_id)
            if cand and cand.download_url:
                return await _download_direct_stream(cand.download_url, output_path)

    # Check for Pexels video webpage
    if "pexels.com" in domain:
        match = re.search(r"/videos?/(?:.*[-/])?(\d+)/?$", path)
        if match:
            media_id = match.group(1)
            logger.info("Resolving Pexels page fallback via API for media ID: %s", media_id)
            from app.providers.pexels import PexelsFootageProvider
            provider = PexelsFootageProvider()
            cand = await asyncio.to_thread(provider.get_by_id, media_id)
            if cand and cand.download_url:
                return await _download_direct_stream(cand.download_url, output_path)

    return None


async def fetch_video_from_url(url: str, output_path: Path) -> Path:
    """Unified video download utility supporting arbitrary video URLs and direct streams.

    Features:
    - Direct video stream detection with browser User-Agent spoofing to bypass 403 Forbidden.
    - Programmatic yt-dlp invocation for rich media platforms (YouTube, Vimeo, social media, etc.).
    - Pixabay/Pexels API fallback when anti-bot Cloudflare challenges block webpage scraping.
    - Post-download FFprobe/FFmpeg container & video stream integrity verification.

    Args:
        url: Direct video URL or webpage containing video.
        output_path: Target Path to store the validated video file.

    Returns:
        Path to the validated, ready-to-stitch video file.

    Raises:
        InvalidInputError: If URL is invalid, download fails, or probe validation fails.
    """
    clean_url = str(url or "").strip()
    if not clean_url:
        raise InvalidInputError("Video URL must not be empty.")

    if any(bad in clean_url for bad in ("..", "file://", "ftp://")):
        raise InvalidInputError("Disallowed characters or protocol in video URL.")

    parsed = urllib.parse.urlparse(clean_url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise InvalidInputError(f"Unsupported URL scheme '{parsed.scheme}'. Only http:// and https:// are permitted.")

    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    downloaded_file: Optional[Path] = None

    # Step A: Direct raw video file check
    is_direct = await _is_direct_video_url(clean_url, parsed)
    if is_direct:
        try:
            logger.info("Attempting direct video stream download with spoofed browser headers: %s", clean_url[:80])
            downloaded_file = await _download_direct_stream(clean_url, output_path)
        except InvalidInputError as input_err:
            if "exceeds maximum allowed size" in input_err.message:
                raise
            logger.warning("Direct video download indicated non-video (%s); falling back to yt-dlp: %s", input_err, clean_url[:80])
            downloaded_file = None
        except Exception as direct_err:
            logger.warning("Direct video download failed (%s); falling back to yt-dlp: %s", direct_err, clean_url[:80])
            downloaded_file = None

    # Step B: Webpage or fallback download via yt-dlp
    if not downloaded_file:
        try:
            logger.info("Invoking yt-dlp for video extraction: %s", clean_url[:80])
            downloaded_file = await asyncio.to_thread(_execute_ytdlp, clean_url, output_path)
        except InvalidInputError as ytdlp_err:
            # Check if this is a Pixabay/Pexels link affected by Cloudflare 403
            logger.warning("yt-dlp extraction failed: %s. Checking stock provider fallback.", ytdlp_err.message)
            fallback_file = await _resolve_stock_webpage_fallback(clean_url, parsed, output_path)
            if fallback_file:
                downloaded_file = fallback_file
            else:
                raise ytdlp_err
        except Exception as e:
            fallback_file = await _resolve_stock_webpage_fallback(clean_url, parsed, output_path)
            if fallback_file:
                downloaded_file = fallback_file
            else:
                clean_err = _clean_error_message(str(e))
                raise InvalidInputError(f"Failed to fetch video from URL: {clean_err}")

    # Step C: Validate the resulting file with FFprobe / probe_media
    if not downloaded_file or not downloaded_file.exists():
        raise InvalidInputError("Downloaded video file does not exist.")

    probe = probe_media(downloaded_file)
    if not probe.get("is_valid") or not probe.get("has_video"):
        err_msg = probe.get("error") or "Downloaded file does not contain a valid video stream."
        try:
            downloaded_file.unlink(missing_ok=True)
        except Exception:
            pass
        raise InvalidInputError(f"Video validation failed: {err_msg}")

    return downloaded_file
