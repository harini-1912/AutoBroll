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
from app.services.text_parser import parse_visual_query, drop_adjectives_to_base_nouns


class PixabayFootageProvider(BaseFootageProvider):
    """Secondary stock video footage provider using Pixabay Video API."""

    BASE_URL = "https://pixabay.com/api/videos/"

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: float = 15.0,
        max_size_bytes: int = 500 * 1024 * 1024,
    ):
        self.api_key = (api_key if api_key is not None else settings.PIXABAY_API_KEY or "").strip().strip('"').strip("'")
        self.timeout = timeout
        self.max_size_bytes = max_size_bytes
        self.provider_name = "pixabay"

    def is_configured(self) -> bool:
        """Check whether valid API credentials are present."""
        return bool(self.api_key and self.api_key.strip())

    def search(
        self,
        query: str,
        min_duration: float = 3.0,
        avoid_terms: Optional[List[str]] = None,
        context: Optional[str] = None,
        primary_domain: Optional[str] = None,
        **kwargs,
    ) -> List[FootageCandidate]:
        """Search Pixabay for relevant licence-clean commercial stock video clips.
        
        Applies context-aware visual query translation and keyword cleaning before querying Pixabay API.
        If 0 hits return, dynamically retries by falling back to {primary_domain} {base_noun} rather
        than dropping domain context.
        Loops through results to select high-quality medium/large MP4 video streams.
        """
        if not self.is_configured():
            logger.debug("PixabayFootageProvider: No API key configured; skipping.")
            return []

        clean_query = query.strip()
        if not clean_query:
            clean_query = "nature"

        # 1. Context-aware visual query refinement layer
        refined_query = parse_visual_query(
            clean_query,
            context=context,
            primary_domain=primary_domain,
        )
        logger.info("Pixabay search: raw query '%s' -> visual query '%s' (primary_domain='%s')", clean_query[:40], refined_query, primary_domain)

        avoid = [a.lower().strip() for a in (avoid_terms or []) if a.strip()]
        params = {
            "key": self.api_key,
            "q": refined_query[:80],
            "per_page": 20,
            "safesearch": "true",
        }

        data = None
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(self.BASE_URL, params=params)
                if resp.status_code in (400, 401, 403):
                    logger.warning("Pixabay API returned status %d", resp.status_code)
                    return []
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning("Pixabay API request failed for query '%s': %s", refined_query[:30], str(e))
            return []

        hits = data.get("hits", []) if data else []

        # 2. Dynamic retry: If 0 hits return, fall back to {primary_domain} {base_noun} rather than dropping the domain context
        base_query = None
        if not hits:
            base_query = drop_adjectives_to_base_nouns(refined_query, primary_domain=primary_domain)
            if base_query and base_query.lower() != refined_query.lower():
                logger.info("Pixabay 0 hits for '%s'; dynamically retrying with domain-anchored base query '%s'", refined_query, base_query)
                retry_params = {
                    "key": self.api_key,
                    "q": base_query[:80],
                    "per_page": 20,
                    "safesearch": "true",
                }
                try:
                    with httpx.Client(timeout=self.timeout) as client:
                        retry_resp = client.get(self.BASE_URL, params=retry_params)
                        if retry_resp.status_code == 200:
                            hits = retry_resp.json().get("hits", [])
                except Exception as retry_err:
                    logger.debug("Pixabay dynamic retry with base nouns failed: %s", str(retry_err))

        # Secondary fallback if still 0 hits and raw query differs from both (anchored with domain if available)
        if not hits:
            raw_fallback = f"{primary_domain} {clean_query}" if primary_domain and primary_domain not in clean_query.lower() else clean_query
            if raw_fallback.lower() not in (refined_query.lower(), (base_query or "").lower()):
                logger.info("Pixabay: 0 hits for '%s', retrying fallback with raw '%s'", refined_query[:30], raw_fallback[:30])
                fallback_params = {
                    "key": self.api_key,
                    "q": raw_fallback[:80],
                    "per_page": 20,
                    "safesearch": "true",
                }
                try:
                    with httpx.Client(timeout=self.timeout) as client:
                        fb_resp = client.get(self.BASE_URL, params=fallback_params)
                        if fb_resp.status_code == 200:
                            hits = fb_resp.json().get("hits", [])
                except Exception as fb_err:
                    logger.debug("Pixabay fallback query failed: %s", str(fb_err))

        candidates: List[FootageCandidate] = []
        for hit in hits:
            cand = self._parse_hit(hit, avoid=avoid)
            if cand:
                candidates.append(cand)

        logger.info("Pixabay search returned %d valid candidates for '%s'", len(candidates), refined_query[:30])
        return candidates

    def _parse_hit(
        self,
        hit: Dict[str, Any],
        avoid: Optional[List[str]] = None,
    ) -> Optional[FootageCandidate]:
        """Safely parse a Pixabay video API hit into a validated FootageCandidate."""
        avoid_list = avoid or []
        preferred_resolutions = ["large", "medium", "small", "tiny"]

        h_id = str(hit.get("id", ""))
        page_url = hit.get("pageURL") or f"https://pixabay.com/videos/id-{h_id}/"
        user_name = hit.get("user") or "Pixabay Creator"
        duration = float(hit.get("duration", 0.0))
        tag_string = hit.get("tags", "") or ""
        h_tags = [t.strip().lower() for t in tag_string.split(",") if t.strip()]

        # Check avoid terms against tags, URL, and creator name
        check_text = f"{tag_string} {user_name} {page_url}".lower()
        if any(term in check_text for term in avoid_list):
            return None

        # Pick optimal video stream from 'videos' object, prioritizing 1080p/720p (avoids 4K processing bottlenecks)
        videos_obj = hit.get("videos", {}) or {}
        chosen_video = None

        # Pass 1: Select crisp HD streams (width between 960 and 1920)
        for res_key in ["large", "medium", "small"]:
            stream = videos_obj.get(res_key)
            if not stream or not isinstance(stream, dict):
                continue
            v_url = stream.get("url")
            if not v_url or not isinstance(v_url, str) or not (v_url.startswith("http://") or v_url.startswith("https://")):
                continue
            w = stream.get("width", 0)
            h = stream.get("height", 0)
            if 960 <= w <= 1920 and h > 0:
                stream_size = stream.get("size")
                if not (stream_size and isinstance(stream_size, (int, float)) and stream_size > self.max_size_bytes):
                    chosen_video = stream
                    break

        # Pass 2: Fallback if no 1080p/720p stream found
        if not chosen_video:
            for res_key in ["medium", "small", "large", "tiny"]:
                stream = videos_obj.get(res_key)
                if not stream or not isinstance(stream, dict):
                    continue
                v_url = stream.get("url")
                if not v_url or not isinstance(v_url, str) or not (v_url.startswith("http://") or v_url.startswith("https://")):
                    continue
                w = stream.get("width", 0)
                h = stream.get("height", 0)
                if w > 0 and h > 0:
                    chosen_video = stream
                    break

        if not chosen_video or not chosen_video.get("url"):
            return None

        download_url = chosen_video.get("url")
        return FootageCandidate(
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

    def get_by_id(self, media_id: str) -> Optional[FootageCandidate]:
        """Fetch footage candidate directly by numeric Pixabay media ID."""
        if not self.is_configured():
            logger.debug("PixabayFootageProvider: No API key configured; skipping get_by_id.")
            return None

        clean_id = str(media_id).strip()
        params = {
            "key": self.api_key,
            "id": clean_id,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(self.BASE_URL, params=params)
                if resp.status_code != 200:
                    logger.warning("Pixabay API get_by_id (%s) returned status %d", clean_id, resp.status_code)
                    return None
                data = resp.json()
        except Exception as e:
            logger.warning("Pixabay API get_by_id failed for ID '%s': %s", clean_id, str(e))
            return None

        hits = data.get("hits", []) if data else []
        for hit in hits:
            cand = self._parse_hit(hit)
            if cand:
                return cand
        return None

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
