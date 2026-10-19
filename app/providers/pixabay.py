"""Pixabay Video API footage provider conforming to MH-3."""
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx

from app.core.config import settings
from app.core.logging import logger
from app.providers.base import BaseFootageProvider, FootageCandidate


class PixabayFootageProvider(BaseFootageProvider):
    """Secondary stock video footage provider using Pixabay Video API."""

    BASE_URL = "https://pixabay.com/api/videos/"

    def __init__(self, api_key: Optional[str] = None, timeout: float = 15.0):
        self.api_key = api_key if api_key is not None else settings.PIXABAY_API_KEY
        self.timeout = timeout
        self.provider_name = "pixabay"

    def is_configured(self) -> bool:
        """Check whether valid API credentials are present."""
        return bool(self.api_key and self.api_key.strip())

    def search(
        self,
        query: str,
        min_duration: float = 3.0,
        avoid_terms: Optional[List[str]] = None,
    ) -> List[FootageCandidate]:
        """Search Pixabay for relevant licence-clean commercial stock video clips."""
        if not self.is_configured():
            logger.debug("PixabayFootageProvider: No API key configured; skipping.")
            return []

        clean_query = query.strip()
        if not clean_query:
            clean_query = "nature"

        avoid = [a.lower().strip() for a in (avoid_terms or []) if a.strip()]
        params = {
            "key": self.api_key,
            "q": clean_query[:80],
            "per_page": 10,
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(self.BASE_URL, params=params)
                if resp.status_code in (400, 401, 403):
                    logger.warning("Pixabay API returned status %d", resp.status_code)
                    return []
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning("Pixabay API request failed for query '%s': %s", clean_query[:30], str(e))
            return []

        hits = data.get("hits", [])
        candidates: List[FootageCandidate] = []

        for hit in hits:
            h_id = str(hit.get("id", ""))
            page_url = hit.get("pageURL") or f"https://pixabay.com/videos/id-{h_id}/"
            user_name = hit.get("user") or "Pixabay Creator"
            duration = float(hit.get("duration", 0.0))
            tag_string = hit.get("tags", "") or ""
            h_tags = [t.strip().lower() for t in tag_string.split(",") if t.strip()]

            # Check avoid terms
            if any(term in tag_string.lower() for term in avoid):
                continue

            # Pick best video stream from 'videos' object
            videos_obj = hit.get("videos", {}) or {}
            chosen_video = videos_obj.get("large") or videos_obj.get("medium") or videos_obj.get("small") or videos_obj.get("tiny")
            if not chosen_video or not chosen_video.get("url"):
                continue

            download_url = chosen_video.get("url")
            cand = FootageCandidate(
                clip_id=f"pixabay_{h_id}",
                provider="pixabay",
                source_url=page_url,
                download_url=download_url,
                licence="Pixabay Content License (Free for commercial use, no attribution required)",
                attribution=f"Video by {user_name} from Pixabay",
                is_ai_generated=False,
                duration=duration,
                width=chosen_video.get("width", 1920),
                height=chosen_video.get("height", 1080),
                fps=30.0,
                tags=h_tags,
                thumbnail_url=chosen_video.get("thumbnail") or hit.get("userImageURL"),
                is_real_footage=True,
            )
            candidates.append(cand)

        logger.info("Pixabay search returned %d valid candidates for '%s'", len(candidates), clean_query[:30])
        return candidates

    def download_clip(
        self,
        candidate: FootageCandidate,
        target_path: str,
        timeout: float = 30.0,
        max_bytes: int = 500 * 1024 * 1024,
    ) -> str:
        """Download remote video clip safely with atomic staging and size validation."""
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists() and target.stat().st_size > 5000:
            return str(target)

        part_file = target.with_suffix(".part")
        if part_file.exists():
            part_file.unlink(missing_ok=True)

        logger.info("Downloading Pixabay clip %s from %s", candidate.clip_id, candidate.download_url[:60])
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

            if target.exists():
                target.unlink()
            shutil.move(str(part_file), str(target))
            logger.info("Successfully downloaded clip %s (%d bytes)", candidate.clip_id, target.stat().st_size)
            return str(target)
        except Exception as e:
            if part_file.exists():
                part_file.unlink(missing_ok=True)
            logger.error("Failed to download Pixabay clip %s: %s", candidate.clip_id, str(e))
            raise
