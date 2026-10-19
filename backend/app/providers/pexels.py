"""Pexels Video API footage provider conforming to MH-3."""
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx

from app.core.config import settings
from app.core.exceptions import FootageNotFoundError
from app.core.logging import logger
from app.providers.base import BaseFootageProvider, FootageCandidate


class PexelsFootageProvider(BaseFootageProvider):
    """Primary stock video footage provider using Pexels Video API."""

    BASE_URL = "https://api.pexels.com/videos/search"

    def __init__(self, api_key: Optional[str] = None, timeout: float = 15.0):
        self.api_key = api_key if api_key is not None else settings.PEXELS_API_KEY
        self.timeout = timeout
        self.provider_name = "pexels"

    def is_configured(self) -> bool:
        """Check whether valid API credentials are present."""
        return bool(self.api_key and self.api_key.strip())

    def search(
        self,
        query: str,
        min_duration: float = 3.0,
        avoid_terms: Optional[List[str]] = None,
    ) -> List[FootageCandidate]:
        """Search Pexels for relevant licence-clean commercial stock video clips."""
        if not self.is_configured():
            logger.debug("PexelsFootageProvider: No API key configured; skipping.")
            return []

        clean_query = query.strip()
        if not clean_query:
            clean_query = "nature landscape"

        avoid = [a.lower().strip() for a in (avoid_terms or []) if a.strip()]
        headers = {
            "Authorization": self.api_key,
            "User-Agent": "IncuBrix-B-Roll-Engine/1.0",
        }
        params = {
            "query": clean_query[:80],
            "per_page": 10,
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(self.BASE_URL, headers=headers, params=params)
                if resp.status_code == 401:
                    logger.warning("Pexels API authentication failed (401 Unauthorized)")
                    return []
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning("Pexels API request failed for query '%s': %s", clean_query[:30], str(e))
            return []

        videos = data.get("videos", [])
        candidates: List[FootageCandidate] = []

        for v in videos:
            v_id = str(v.get("id", ""))
            v_duration = float(v.get("duration", 0.0))
            v_url = v.get("url", f"https://www.pexels.com/video/{v_id}")
            user_info = v.get("user", {}) or {}
            user_name = user_info.get("name") or "Pexels Creator"
            user_url = user_info.get("url") or "https://www.pexels.com"
            v_tags = [t.lower() for t in v.get("tags", []) if isinstance(t, str)]

            # Check avoid terms
            if any(term in " ".join(v_tags) or term in v_url.lower() for term in avoid):
                continue

            # Pick best video file (prefer mp4, HD width >= 1280)
            video_files = v.get("video_files", []) or []
            mp4_files = [
                vf for vf in video_files
                if vf.get("file_type") == "video/mp4" or (vf.get("link") and ".mp4" in vf.get("link", ""))
            ]
            if not mp4_files:
                mp4_files = video_files

            if not mp4_files:
                continue

            # Sort by width descending to get best resolution, prefer 1920 or 1280
            best_file = max(
                mp4_files,
                key=lambda f: (f.get("width", 0) <= 1920, f.get("width", 0)),
            )

            download_link = best_file.get("link")
            if not download_link:
                continue

            cand = FootageCandidate(
                clip_id=f"pexels_{v_id}",
                provider="pexels",
                source_url=v_url,
                download_url=download_link,
                licence="Pexels License (Free for commercial use, no attribution required)",
                attribution=f"Video by {user_name} on Pexels ({user_url})",
                is_ai_generated=False,
                duration=v_duration,
                width=best_file.get("width") or v.get("width", 1920),
                height=best_file.get("height") or v.get("height", 1080),
                fps=float(best_file.get("fps") or 30.0),
                tags=v_tags,
                thumbnail_url=v.get("image"),
                is_real_footage=True,
            )
            candidates.append(cand)

        logger.info("Pexels search returned %d valid candidates for '%s'", len(candidates), clean_query[:30])
        return candidates

    def download_clip(
        self,
        candidate: FootageCandidate,
        target_path: str,
        timeout: float = 30.0,
        max_bytes: int = 100 * 1024 * 1024,
    ) -> str:
        """Download remote video clip safely with atomic staging and size validation."""
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        # If already completely downloaded, reuse
        if target.exists() and target.stat().st_size > 5000:
            return str(target)

        part_file = target.with_suffix(".part")
        if part_file.exists():
            part_file.unlink(missing_ok=True)

        logger.info("Downloading Pexels clip %s from %s", candidate.clip_id, candidate.download_url[:60])
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                with client.stream("GET", candidate.download_url) as resp:
                    resp.raise_for_status()
                    total_bytes = 0
                    with open(part_file, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=65536):
                            total_bytes += len(chunk)
                            if total_bytes > max_bytes:
                                raise ValueError(f"Stock clip exceeds maximum permitted size of {max_bytes} bytes")
                            f.write(chunk)

            if not part_file.exists() or part_file.stat().st_size < 1000:
                raise ValueError("Downloaded clip is empty or corrupt")

            # Atomic promotion to final target path
            if target.exists():
                target.unlink()
            shutil.move(str(part_file), str(target))
            logger.info("Successfully downloaded clip %s (%d bytes)", candidate.clip_id, target.stat().st_size)
            return str(target)
        except Exception as e:
            if part_file.exists():
                part_file.unlink(missing_ok=True)
            logger.error("Failed to download Pexels clip %s: %s", candidate.clip_id, str(e))
            raise
