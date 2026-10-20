"""Footage Provider and Download Cache Management Service.

Enforces:
1. Strict Per-Job Directory Isolation: Prevents media caching collisions across concurrent or sequential jobs.
2. Explicit Caching Key: Cache entries are strictly keyed by (provider, exact_query_hash, job_id).
3. Fresh Footage Verification: Validates that every candidate and rendered MP4 was created during the current job run.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from app.core.config import settings
    from app.core.exceptions import FootageNotFoundError, RenderFailedError
    from app.core.logging import logger
    from app.providers.base import BaseFootageProvider, FootageCandidate
except ImportError:
    from backend.app.core.config import settings
    from backend.app.core.exceptions import FootageNotFoundError, RenderFailedError
    from backend.app.core.logging import logger
    from backend.app.providers.base import BaseFootageProvider, FootageCandidate


class FootageProviderService:
    """Manages footage retrieval, download isolation, and per-job cache enforcement."""

    def __init__(self):
        # In-memory clip cache strictly keyed by (provider, exact_query_hash, job_id)
        self._clip_cache: Dict[Tuple[str, str, str], Path] = {}

    @staticmethod
    def get_query_hash(query: str) -> str:
        """Compute deterministic hash of the exact search query."""
        clean = (query or "").strip().lower()
        return hashlib.sha256(clean.encode("utf-8")).hexdigest()[:16]

    def get_cached_clip(
        self,
        provider: str,
        query: str,
        job_id: str,
        disallowed_paths: Optional[Set[str]] = None,
    ) -> Optional[Path]:
        """Retrieve a cached clip path ONLY if explicitly keyed to the current job_id.

        Prevents serving cached file paths from previous unrelated jobs,
        and avoids returning a clip that has already been assigned to another scene in this job.
        """
        query_hash = self.get_query_hash(query)
        cache_key = (provider.lower(), query_hash, str(job_id))
        cached_path = self._clip_cache.get(cache_key)

        if cached_path and cached_path.exists() and cached_path.stat().st_size > 0:
            if disallowed_paths and (str(cached_path) in disallowed_paths or str(cached_path.resolve()) in disallowed_paths):
                logger.info(
                    "Cached clip %s already assigned to an earlier scene in job %s; skipping to ensure uniqueness",
                    cached_path.name,
                    job_id,
                )
                return None
            logger.info(
                "Serving isolated cached clip for (%s, %s, %s): %s",
                provider,
                query_hash,
                job_id,
                cached_path.name,
            )
            return cached_path
        return None

    def store_cached_clip(
        self,
        provider: str,
        query: str,
        job_id: str,
        clip_path: Path,
    ) -> None:
        """Store clip in cache keyed explicitly by (provider, exact_query_hash, job_id)."""
        query_hash = self.get_query_hash(query)
        cache_key = (provider.lower(), query_hash, str(job_id))
        self._clip_cache[cache_key] = Path(clip_path)

    def clear_job_cache(self, job_id: str) -> None:
        """Purge all cached clips associated with a specific job_id."""
        keys_to_remove = [k for k in self._clip_cache if k[2] == str(job_id)]
        for k in keys_to_remove:
            self._clip_cache.pop(k, None)

    @staticmethod
    def verify_input_clip_freshness(
        clip_path: Path,
        job_start_time: float,
        job_dir: Optional[Path] = None,
    ) -> bool:
        """Verify that an input clip was created during the current job run.

        Checks:
        1. File exists and has positive size.
        2. File mtime is greater than or equal to job_start_time (allowing 2s margin for filesystem clock skew).
        3. If job_dir is provided, verify strict containment inside job_dir.
        """
        clip_path = Path(clip_path)
        if not clip_path.exists():
            return False

        if clip_path.stat().st_size == 0:
            return False

        # Verify creation/modification timestamp is within current job execution window
        file_mtime = clip_path.stat().st_mtime
        if file_mtime < (job_start_time - 2.0):
            logger.warning(
                "Clip %s mtime (%.2f) predates job start time (%.2f); rejecting as stale",
                clip_path.name,
                file_mtime,
                job_start_time,
            )
            return False

        # Verify containment if job_dir provided
        if job_dir:
            try:
                clip_path.resolve().relative_to(Path(job_dir).resolve())
            except ValueError:
                logger.warning(
                    "Clip %s lies outside job directory %s; rejecting as cross-job contamination",
                    clip_path,
                    job_dir,
                )
                return False

        return True


footage_provider_service = FootageProviderService()
