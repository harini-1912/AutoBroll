"""Multi-Tier AI and Motion Graphic Fallback Generator.

Implements a 4-tier visual fallback cascade:
Tier 1: Pixabay Stock Footage (Primary)
Tier 2: Pexels Stock Footage (Secondary)
Tier 3a: Pollinations AI Video (Prompt-to-video via gen.pollinations.ai)
Tier 3b: Pollinations AI Image + FFmpeg Ken Burns Zoompan
Tier 4: 100% Offline Local FFmpeg Animated Motion Graphic (gradients / mptestsrc)
"""
import asyncio
import logging
import os
import subprocess
import urllib.parse
from pathlib import Path
from typing import Optional, Tuple

import httpx

try:
    from app.core.config import settings
except ImportError:
    from backend.app.core.config import settings

logger = logging.getLogger(__name__)

ASPECT_DIMENSIONS = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "1:1": (720, 720),
}

VIDEO_MODEL_DIMENSIONS = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "1:1": (720, 720),
}


def _is_valid_mp4(data: bytes) -> bool:
    """Verify raw bytes represent a plausible MP4 container."""
    if len(data) < 1000:
        return False
    # Check for ftyp signature or ISO base media file box
    if b"ftyp" in data[:64] or data[:4] == b"\x00\x00\x00":
        return True
    return False


def _is_valid_image(data: bytes) -> bool:
    """Verify raw bytes represent a valid JPEG, PNG, or WebP image."""
    if len(data) < 500:
        return False
    # JPEG magic number
    if data.startswith(b"\xff\xd8\xff"):
        return True
    # PNG magic number
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    # WebP magic number (RIFF....WEBP)
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return True
    return False


import random

PREMIUM_GRADIENT_PALETTES = [
    ("0x0a1128", "0x1c2541", "0x3a506b"),  # Midnight Navy
    ("0x0b1d13", "0x133c27", "0x1b5e3a"),  # Obsidian Emerald
    ("0x1f1214", "0x38181c", "0x5c242a"),  # Charcoal Crimson
    ("0x150b24", "0x261442", "0x3d2066"),  # Royal Violet
    ("0x0e171b", "0x182a32", "0x26424e"),  # Gunmetal Cyan
]


async def generate_visual_fallback(
    prompt: str,
    output_path: Path,
    scene_duration: float = 5.0,
    aspect_ratio: str = "16:9",
    scene_index: int = 0,
) -> Path:
    """Generate a visual fallback video asset using a multi-tier pipeline.

    Flow:
    a) Pollinations AI Video Attempt:
       If POLLINATIONS_API_KEY is present, call
       https://gen.pollinations.ai/image/{encoded_prompt}?model=video&width=1280&height=720&seed={seed}
       with Authorization: Bearer {POLLINATIONS_API_KEY} (timeout: 20s).
       If valid MP4 data received, write directly to output_path.

    b) Pollinations AI Image + Ken Burns Zoom (Fast Backup):
       If video call times out or fails (or only image generation succeeds):
       Fetch an image from https://gen.pollinations.ai/image/{encoded_prompt}?width=1920&height=1080&seed={seed}
       or https://image.pollinations.ai/prompt/{encoded_prompt}.
       Convert downloaded image into moving 1080p MP4 via FFmpeg zoompan.

    c) 100% Offline Local Backup (If no internet/keys available):
       If all external API calls fail or no keys set, generate animated dark gradient
       with subtle motion using FFmpeg's gradients filter so engine never fails.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    scene_duration = max(1.0, float(scene_duration))
    clean_prompt = prompt.strip() or "cinematic abstract background"
    encoded_prompt = urllib.parse.quote(clean_prompt)
    seed = random.randint(100000, 999999)

    width, height = ASPECT_DIMENSIONS.get(aspect_ratio, (1920, 1080))
    vid_w, vid_h = VIDEO_MODEL_DIMENSIONS.get(aspect_ratio, (1280, 720))

    api_key = getattr(settings, "POLLINATIONS_API_KEY", None) or os.getenv("POLLINATIONS_API_KEY") or None

    # -------------------------------------------------------------------------
    # Tier a: Pollinations AI Video Attempt (Requires POLLINATIONS_API_KEY)
    # -------------------------------------------------------------------------
    if api_key:
        video_url = (
            f"https://gen.pollinations.ai/image/{encoded_prompt}"
            f"?model=video&width={vid_w}&height={vid_h}&seed={seed}"
        )
        headers = {"Authorization": f"Bearer {api_key}"}
        logger.info("Attempting Pollinations AI Video generation for: '%s' (seed=%d)", clean_prompt[:40], seed)
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                resp = await client.get(video_url, headers=headers)
                if resp.status_code == 200 and _is_valid_mp4(resp.content):
                    output_path.write_bytes(resp.content)
                    logger.info("Pollinations AI Video generated successfully: %s", output_path)
                    return output_path
                else:
                    logger.warning(
                        "Pollinations video response invalid (status=%d, bytes=%d); cascading to image fallback",
                        resp.status_code,
                        len(resp.content),
                    )
        except Exception as exc:
            logger.warning("Pollinations AI Video call failed (%s); cascading to Ken Burns image fallback", str(exc))

    # -------------------------------------------------------------------------
    # Tier b: Pollinations AI Image + Ken Burns Zoompan MP4 (Fast Backup)
    # -------------------------------------------------------------------------
    logger.info("Attempting Pollinations AI Image + Ken Burns zoom for: '%s' (seed=%d)", clean_prompt[:40], seed)
    image_urls = [
        f"https://gen.pollinations.ai/image/{encoded_prompt}?width={width}&height={height}&seed={seed}&nologo=true",
        f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&seed={seed}&nologo=true",
    ]
    img_headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    img_data: Optional[bytes] = None

    for img_url in image_urls:
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(img_url, headers=img_headers)
                if resp.status_code == 200 and _is_valid_image(resp.content):
                    img_data = resp.content
                    logger.info("Successfully fetched AI fallback image from %s", img_url.split("?")[0])
                    break
        except Exception as exc:
            logger.debug("Pollinations image fetch from %s failed: %s", img_url.split("?")[0], str(exc))

    if img_data:
        tmp_img = output_path.with_suffix(".tmp_ai.jpg")
        try:
            tmp_img.write_bytes(img_data)
            d_frames = max(25, int(scene_duration * 25))
            # Vary Ken Burns motion direction based on scene_index to ensure cinematic variety
            motion_mode = scene_index % 3
            if motion_mode == 0:
                vf_zoompan = f"zoompan=z='min(zoom+0.0015,1.25)':d={d_frames}:s={width}x{height}:fps=25"
            elif motion_mode == 1:
                vf_zoompan = f"zoompan=z='1.15':x='if(lte(on,1),(iw-iw/zoom)/2,min(x+1,iw-iw/zoom))':y='(ih-ih/zoom)/2':d={d_frames}:s={width}x{height}:fps=25"
            else:
                vf_zoompan = f"zoompan=z='max(1.25-0.0015*on,1.0)':d={d_frames}:s={width}x{height}:fps=25"

            cmd_zoom = [
                settings.FFMPEG_PATH,
                "-y",
                "-threads",
                "1",
                "-loop",
                "1",
                "-i",
                str(tmp_img),
                "-vf",
                vf_zoompan,
                "-t",
                str(scene_duration),
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-bufsize",
                "1000k",
                "-maxrate",
                "1500k",
                "-pix_fmt",
                "yuv420p",
                str(output_path),
            ]
            res = await asyncio.to_thread(subprocess.run, cmd_zoom, capture_output=True, text=True, timeout=60)
            if res.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1000:
                logger.info("Generated Ken Burns zoompan fallback clip: %s", output_path)
                return output_path
            else:
                logger.warning("Ken Burns FFmpeg conversion failed (%s); cascading to offline motion graphic", res.stderr[:120])
        except Exception as exc:
            logger.warning("Error creating Ken Burns zoompan clip (%s); cascading to offline motion graphic", str(exc))
        finally:
            tmp_img.unlink(missing_ok=True)

    # -------------------------------------------------------------------------
    # Tier c: 100% Offline Local Backup (Animated Dark Gradient Motion Graphic)
    # -------------------------------------------------------------------------
    logger.info("Generating 100% offline local motion graphic fallback for scene (%.2fs)", scene_duration)
    return await generate_offline_gradient_clip(
        output_path=output_path,
        scene_duration=scene_duration,
        width=width,
        height=height,
        scene_index=scene_index,
    )


async def generate_offline_gradient_clip(
    output_path: Path,
    scene_duration: float = 5.0,
    width: int = 1280,
    height: int = 720,
    scene_index: int = 0,
) -> Path:
    """Generate an animated dark gradient clip with subtle motion using FFmpeg."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    scene_duration = max(1.0, float(scene_duration))

    palette = PREMIUM_GRADIENT_PALETTES[scene_index % len(PREMIUM_GRADIENT_PALETTES)]
    c0, c1, c2 = palette
    speed = round(0.01 + (scene_index % 3) * 0.005, 3)

    grad_filter = (
        f"gradients=s={width}x{height}:d={scene_duration}:"
        f"c0={c0}:c1={c1}:c2={c2}:"
        f"x0=0:y0=0:x1={width}:y1={height}:speed={speed}"
    )
    cmd_grad = [
        settings.FFMPEG_PATH,
        "-y",
        "-threads",
        "1",
        "-f",
        "lavfi",
        "-i",
        grad_filter,
        "-t",
        str(scene_duration),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-bufsize",
        "1000k",
        "-maxrate",
        "1500k",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "25",
        str(output_path),
    ]

    try:
        proc = await asyncio.to_thread(subprocess.run, cmd_grad, capture_output=True, text=True, timeout=60)
        if proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            logger.info("Generated offline FFmpeg gradient fallback: %s (palette=%s)", output_path, palette)
            return output_path
        else:
            logger.warning("FFmpeg gradients filter failed (%s); trying color backup", proc.stderr[:120])
    except Exception as exc:
        logger.warning("FFmpeg gradients execution failed: %s", str(exc))

    # Ultimate offline backup: solid dark color backdrop
    cmd_backup = [
        settings.FFMPEG_PATH,
        "-y",
        "-threads",
        "1",
        "-f",
        "lavfi",
        "-i",
        f"color=c={c1}:s={width}x{height}:r=25:d={scene_duration}",
        "-t",
        str(scene_duration),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-bufsize",
        "1000k",
        "-maxrate",
        "1500k",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    await asyncio.to_thread(subprocess.run, cmd_backup, capture_output=True, text=True, timeout=60)
    return output_path
