"""Dedicated Phase 3D tests for SceneSwapper selective scene clip replacement (MH-5).

Covers:
 1.  Successful replacement of a valid scene
 2.  Missing job raises JobNotFoundError
 3.  Missing scene raises SceneNotFoundError
 4.  Path-traversal in job_id / scene_id is rejected
 5.  Job in queued/processing state is rejected
 6.  Job with no timeline is rejected
 7.  Provider failure falls back to procedural visual (mock)
 8.  Only the target scene clip is re-rendered; unaffected clips are reused
 9.  Final videos are reassembled for every requested aspect ratio
10.  Narration audio is preserved; manifest and timeline are updated
11.  Invalid (dummy) replacement clip is detected and rejected
12.  Failed replacement does not corrupt the previous valid output
13.  Temporary staging directory is cleaned up after success and after failure
14.  Concurrent replacement requests on the same job are blocked (lock strategy)
15.  Custom video_url is validated; unsafe schemes are rejected
16.  Backward-compatible POST /swap endpoint delegates to same logic
"""
import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.core.config import settings
from app.core.database import db
from app.core.exceptions import (
    InvalidInputError,
    JobNotFoundError,
    RenderFailedError,
    SceneNotFoundError,
)
from app.providers.base import FootageCandidate
from app.providers.mock import MockFootageProvider
from app.rendering.ffmpeg_engine import ffmpeg_engine, probe_media
from app.schemas.job import SceneClipReplacementRequest
from app.services.scene_swapper import SceneSwapper, scene_swapper
from app.workers.job_runner import job_runner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_candidate(clip_id: str = "mock_clip_001", is_real: bool = False) -> FootageCandidate:
    return FootageCandidate(
        clip_id=clip_id,
        provider="mock_stock_primary",
        source_url="https://images.incubrix.test/footage/mock",
        download_url="https://images.incubrix.test/footage/mock.mp4",
        licence="CC0 / Commercial Free",
        attribution="Mock Stock Library",
        is_ai_generated=False,
        duration=10.0,
        width=1920,
        height=1080,
        fps=30.0,
        tags=["business", "growth"],
        is_real_footage=is_real,
    )


def _build_completed_job(
    tmp_path: Path,
    job_id: str,
    aspect_ratios: Optional[List[str]] = None,
    n_scenes: int = 3,
) -> Dict[str, Any]:
    """Seed the database and fake file system with a completed 2-scene job."""
    if aspect_ratios is None:
        aspect_ratios = ["16:9", "9:16"]

    scenes = [
        {
            "scene_id": f"scene_{i:03d}",
            "text": f"Scene {i} narration text for testing.",
            "visual_hint": "business office",
            "start_time": float(i * 3),
            "end_time": float(i * 3 + 3),
            "duration": 3.0,
            "clip": {
                "clip_id": "mock_clip_001",
                "provider": "mock_stock_primary",
                "source_url": "https://images.incubrix.test/footage/mock",
                "download_url": None,
                "licence": "CC0",
                "attribution": "Mock Library",
                "is_ai_generated": False,
                "is_real_stock_footage": False,
                "is_fallback": True,
                "media_type": "procedural_visual",
                "duration": 3.0,
                "relevance_score": 4.0,
                "selection_reason": "mock fallback",
                "thumbnail_url": None,
            },
        }
        for i in range(n_scenes)
    ]

    timeline = {
        "total_duration": float(n_scenes * 3),
        "total_scenes": n_scenes,
        "scenes": scenes,
    }
    manifest = {
        "completed_at": "2026-01-01T00:00:00+00:00",
        "licence_and_rights": {
            "commercial_use_cleared": True,
            "all_clips_licensed": True,
            "contains_real_stock_footage": False,
            "contains_ai_media": True,
        },
        "warnings": [],
    }

    req_payload = {
        "script": " ".join([s["text"] for s in scenes]),
        "aspect_ratios": aspect_ratios,
        "voice": "en-IN-female",
        "avoid": [],
    }

    db.create_job(job_id, req_payload)
    job_dir = Path(settings.LOCAL_STORAGE_DIR) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Write synthetic narration.wav (valid WAV header so existence checks pass)
    import wave, struct, math
    narration_path = job_dir / "narration.wav"
    sample_rate = 24000
    duration_s = float(n_scenes * 3)
    with wave.open(str(narration_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(int(sample_rate * duration_s)):
            val = int(8000 * math.sin(2 * math.pi * 440 * i / sample_rate))
            frames.extend(struct.pack("<h", val))
        wf.writeframes(bytes(frames))

    # Write per-aspect-ratio scene clip stubs using FFmpeg if available, else minimal stubs
    for ratio in aspect_ratios:
        ratio_slug = ratio.replace(":", "_")
        scenes_dir = job_dir / f"scenes_{ratio_slug}"
        scenes_dir.mkdir(parents=True, exist_ok=True)
        for i in range(n_scenes):
            clip_path = scenes_dir / f"clip_{i:03d}.mp4"
            if ffmpeg_engine.is_available():
                w, h = (1920, 1080) if ratio == "16:9" else (1080, 1920)
                subprocess.run(
                    [
                        ffmpeg_engine.ffmpeg_path,
                        "-y", "-f", "lavfi",
                        "-i", f"color=c=#1e293b:s={w}x{h}:r=30:d=1",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        str(clip_path),
                    ],
                    capture_output=True, timeout=20,
                )
            else:
                # Tiny valid-enough stub
                clip_path.write_bytes(b"\x00" * 4096)

        # Write stub final video
        final_video = job_dir / f"video_{ratio_slug}.mp4"
        if ffmpeg_engine.is_available() and (job_dir / f"scenes_{ratio_slug}/clip_000.mp4").stat().st_size > 100:
            concat_file = job_dir / f"concat_{ratio_slug}.txt"
            concat_file.write_text(
                "\n".join([f"file 'scenes_{ratio_slug}/clip_{i:03d}.mp4'" for i in range(n_scenes)]) + "\n",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    ffmpeg_engine.ffmpeg_path,
                    "-y", "-f", "concat", "-safe", "0",
                    "-i", str(concat_file.resolve()),
                    "-i", str(narration_path.resolve()),
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-shortest", "-movflags", "+faststart",
                    str(final_video.resolve()),
                ],
                capture_output=True, timeout=30, cwd=str(job_dir),
            )
        else:
            final_video.write_bytes(b"\x00" * 4096)

    result_urls = {}
    base_url = settings.BASE_URL.rstrip("/")
    for ratio in aspect_ratios:
        ratio_slug = ratio.replace(":", "_")
        result_urls[f"video_{ratio_slug}"] = f"{base_url}/api/v1/jobs/download/{job_id}/video_{ratio_slug}.mp4"

    db.complete_job_success(
        job_id=job_id,
        result_urls=result_urls,
        timeline=timeline,
        manifest=manifest,
        warnings=[],
    )
    return db.get_job(job_id)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def swapper() -> SceneSwapper:
    """Fresh SceneSwapper instance per test (no shared lock state)."""
    return SceneSwapper()


@pytest.fixture
def mock_provider():
    """MockFootageProvider wired into job_runner for tests."""
    return MockFootageProvider("mock_stock_primary")


# ---------------------------------------------------------------------------
# 1. Successful replacement of a valid scene
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_successful_scene_replacement(tmp_path, monkeypatch, swapper):
    """Replacing scene_001 returns success and updates timeline/manifest in DB."""
    if not ffmpeg_engine.is_available():
        pytest.skip("FFmpeg binary not available")

    job_id = "swap-test-success-001"
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setattr("app.services.scene_swapper.settings", settings)
    monkeypatch.setattr("app.services.scene_swapper.db", db)

    _build_completed_job(tmp_path / "storage", job_id, aspect_ratios=["16:9", "9:16"], n_scenes=3)

    # Wire mock provider into swapper via job_runner
    mock_prov = MockFootageProvider("mock_stock_primary")
    monkeypatch.setattr(job_runner, "primary_footage_provider", mock_prov)
    monkeypatch.setattr(job_runner, "secondary_footage_provider", MockFootageProvider("mock_secondary"))
    monkeypatch.setattr(job_runner, "fallback_ai_provider", MockFootageProvider("ai_fallback"))

    req = SceneClipReplacementRequest(query_override="office growth")
    result = await swapper.replace_scene_clip(job_id, "scene_001", req)

    assert result["status"] == "completed"
    assert result["scene_id"] == "scene_001"
    assert "scene_001" in result["message"]
    assert set(result["affected_aspect_ratios"]) == {"16:9", "9:16"}

    # DB should now be updated
    record = db.get_job(job_id)
    assert record is not None
    timeline = record["timeline"]
    swapped_scene = timeline["scenes"][1]
    assert swapped_scene["scene_id"] == "scene_001"
    assert swapped_scene["clip"]["clip_id"] is not None

    # Warnings should reference the swap
    assert any("scene_001" in w for w in record["warnings"])


# ---------------------------------------------------------------------------
# 2. Missing job raises JobNotFoundError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_job_raises_not_found(swapper):
    """SceneSwapper raises JobNotFoundError for a job_id not in the database."""
    req = SceneClipReplacementRequest()
    with pytest.raises(JobNotFoundError):
        await swapper.replace_scene_clip("nonexistent-job-xyz", "scene_000", req)


# ---------------------------------------------------------------------------
# 3. Missing scene raises SceneNotFoundError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_scene_raises_not_found(tmp_path, monkeypatch, swapper):
    """SceneSwapper raises SceneNotFoundError when scene_id is not in timeline."""
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    job_id = "swap-test-no-scene-002"
    _build_completed_job(tmp_path / "storage", job_id, n_scenes=2)

    req = SceneClipReplacementRequest()
    with pytest.raises(SceneNotFoundError) as exc_info:
        await swapper.replace_scene_clip(job_id, "scene_999", req)
    assert "scene_999" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 4. Path traversal in job_id / scene_id is rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("bad_job_id", ["../evil", "../../etc/passwd", "job\\..\\etc"])
async def test_path_traversal_in_job_id_is_rejected(bad_job_id, swapper):
    """Path traversal characters in job_id raise InvalidInputError immediately."""
    req = SceneClipReplacementRequest()
    with pytest.raises(InvalidInputError) as exc_info:
        await swapper.replace_scene_clip(bad_job_id, "scene_000", req)
    assert "invalid" in str(exc_info.value).lower() or "characters" in str(exc_info.value).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_scene_id", ["../evil_scene", "scene/../../etc", "scene\\..\\x"])
async def test_path_traversal_in_scene_id_is_rejected(bad_scene_id, tmp_path, monkeypatch, swapper):
    """Path traversal characters in scene_id raise InvalidInputError immediately."""
    req = SceneClipReplacementRequest()
    with pytest.raises(InvalidInputError):
        await swapper.replace_scene_clip("some-valid-job-id", bad_scene_id, req)


# ---------------------------------------------------------------------------
# 5. Job in queued / processing state is rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("active_status", ["queued", "processing"])
async def test_active_job_status_rejected(active_status, tmp_path, monkeypatch, swapper):
    """Swapping clips on a queued or processing job raises InvalidInputError."""
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    job_id = f"swap-test-active-{active_status}"
    req_payload = {"script": "test", "aspect_ratios": ["16:9"]}
    db.create_job(job_id, req_payload)

    # Manually override status to the active one (create_job leaves it at 'queued')
    if active_status == "processing":
        db.start_job(job_id)

    # At this point job has no timeline; regardless, active status must be caught first
    req = SceneClipReplacementRequest()
    with pytest.raises(InvalidInputError) as exc_info:
        await swapper.replace_scene_clip(job_id, "scene_000", req)
    assert active_status in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 6. Job with no timeline is rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_job_without_timeline_rejected(tmp_path, monkeypatch, swapper):
    """A completed job that somehow lacks a timeline raises InvalidInputError."""
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    job_id = "swap-test-no-timeline-006"
    req_payload = {"script": "test", "aspect_ratios": ["16:9"]}
    db.create_job(job_id, req_payload)

    # Mark completed but with empty timeline
    db.complete_job_success(
        job_id=job_id,
        result_urls={},
        timeline={"scenes": []},  # empty scenes list
        manifest={},
        warnings=[],
    )

    req = SceneClipReplacementRequest()
    with pytest.raises(InvalidInputError) as exc_info:
        await swapper.replace_scene_clip(job_id, "scene_000", req)
    assert "timeline" in str(exc_info.value).lower() or "scenes" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 7. Provider failure falls back to procedural visual (mock fallback)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_providers_failing_uses_procedural_fallback(tmp_path, monkeypatch, swapper):
    """When every real provider fails, swapper falls back to procedural visual candidate."""
    if not ffmpeg_engine.is_available():
        pytest.skip("FFmpeg binary not available")

    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    job_id = "swap-test-fallback-007"
    _build_completed_job(tmp_path / "storage", job_id, aspect_ratios=["16:9"], n_scenes=2)

    # All providers return empty
    class EmptyProvider(MockFootageProvider):
        def search(self, *args, **kwargs):
            return []

    monkeypatch.setattr(job_runner, "primary_footage_provider", EmptyProvider())
    monkeypatch.setattr(job_runner, "secondary_footage_provider", EmptyProvider())
    monkeypatch.setattr(job_runner, "fallback_ai_provider", EmptyProvider())

    req = SceneClipReplacementRequest(query_override="anything")
    result = await swapper.replace_scene_clip(job_id, "scene_000", req)

    assert result["status"] == "completed"
    # Procedural fallback clip should be marked as fallback
    assert result["swapped_clip"]["is_fallback"] is True
    assert result["swapped_clip"]["provider"] == "ai_generative_fallback"


# ---------------------------------------------------------------------------
# 8. Only the target scene clip is re-rendered; unaffected clips are reused
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_only_target_scene_is_rerendered(tmp_path, monkeypatch, swapper):
    """Verify render_scene_clip is called exactly once (for target), not for unaffected scenes."""
    if not ffmpeg_engine.is_available():
        pytest.skip("FFmpeg binary not available")

    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    job_id = "swap-test-selective-008"
    n_scenes = 3
    _build_completed_job(tmp_path / "storage", job_id, aspect_ratios=["16:9"], n_scenes=n_scenes)

    monkeypatch.setattr(job_runner, "primary_footage_provider", MockFootageProvider())
    monkeypatch.setattr(job_runner, "secondary_footage_provider", MockFootageProvider())
    monkeypatch.setattr(job_runner, "fallback_ai_provider", MockFootageProvider())

    render_calls: List[str] = []
    original_render = ffmpeg_engine.render_scene_clip

    def tracking_render(scene_id, *args, **kwargs):
        render_calls.append(scene_id)
        return original_render(scene_id, *args, **kwargs)

    monkeypatch.setattr(ffmpeg_engine, "render_scene_clip", tracking_render)

    req = SceneClipReplacementRequest(query_override="technology cloud")
    await swapper.replace_scene_clip(job_id, "scene_001", req)

    # Only scene_001 should have been rendered (once per aspect ratio)
    rendered_scene_ids = set(render_calls)
    assert "scene_001" in rendered_scene_ids, "Target scene must be re-rendered"
    # scene_000 and scene_002 must NOT appear (they should be copied from cache)
    assert "scene_000" not in rendered_scene_ids, "Unaffected scene_000 must NOT be re-rendered"
    assert "scene_002" not in rendered_scene_ids, "Unaffected scene_002 must NOT be re-rendered"


# ---------------------------------------------------------------------------
# 9. Final videos are reassembled for all requested aspect ratios
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_final_videos_produced_for_all_aspect_ratios(tmp_path, monkeypatch, swapper):
    """After a swap both video_16_9.mp4 and video_9_16.mp4 are valid H.264 files."""
    if not ffmpeg_engine.is_available():
        pytest.skip("FFmpeg binary not available")

    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    job_id = "swap-test-ratios-009"
    aspect_ratios = ["16:9", "9:16"]
    _build_completed_job(tmp_path / "storage", job_id, aspect_ratios=aspect_ratios, n_scenes=2)

    monkeypatch.setattr(job_runner, "primary_footage_provider", MockFootageProvider())
    monkeypatch.setattr(job_runner, "secondary_footage_provider", MockFootageProvider())
    monkeypatch.setattr(job_runner, "fallback_ai_provider", MockFootageProvider())

    req = SceneClipReplacementRequest()
    result = await swapper.replace_scene_clip(job_id, "scene_000", req)

    assert set(result["affected_aspect_ratios"]) == set(aspect_ratios)

    job_dir = tmp_path / "storage" / job_id
    for ratio in aspect_ratios:
        ratio_slug = ratio.replace(":", "_")
        final_video = job_dir / f"video_{ratio_slug}.mp4"
        assert final_video.exists(), f"Final video missing for {ratio}"
        assert final_video.stat().st_size > 1000, f"Final video too small for {ratio}"
        probe = probe_media(final_video)
        assert probe["is_valid"] is True, f"video_{ratio_slug}.mp4 is not a valid media file"
        assert probe["has_video"] is True


# ---------------------------------------------------------------------------
# 10. Narration audio preserved; manifest and timeline updated correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_manifest_and_timeline_updated_after_swap(tmp_path, monkeypatch, swapper):
    """After swap, timeline has updated clip metadata and manifest has a swap warning."""
    if not ffmpeg_engine.is_available():
        pytest.skip("FFmpeg binary not available")

    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    job_id = "swap-test-manifest-010"
    _build_completed_job(tmp_path / "storage", job_id, aspect_ratios=["16:9"], n_scenes=2)

    monkeypatch.setattr(job_runner, "primary_footage_provider", MockFootageProvider())
    monkeypatch.setattr(job_runner, "secondary_footage_provider", MockFootageProvider())
    monkeypatch.setattr(job_runner, "fallback_ai_provider", MockFootageProvider())

    req = SceneClipReplacementRequest(query_override="landscape calm")
    await swapper.replace_scene_clip(job_id, "scene_001", req)

    # DB timeline.json should have the updated clip entry
    record = db.get_job(job_id)
    swapped = record["timeline"]["scenes"][1]
    assert swapped["clip"]["clip_id"] is not None
    assert swapped["clip"]["provider"] is not None
    assert "duration" in swapped["clip"]

    # Warning must be recorded
    warnings = record["warnings"]
    assert any("scene_001" in w for w in warnings)

    # Narration file must still exist and have content
    narration = tmp_path / "storage" / job_id / "narration.wav"
    assert narration.exists()
    assert narration.stat().st_size > 0


# ---------------------------------------------------------------------------
# 11. Invalid replacement clip is detected and rejected before promotion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_rendered_clip_raises_render_failed(tmp_path, monkeypatch, swapper):
    """When FFmpeg produces an invalid clip, RenderFailedError is raised."""
    if not ffmpeg_engine.is_available():
        pytest.skip("FFmpeg binary not available")

    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    job_id = "swap-test-invalid-clip-011"
    _build_completed_job(tmp_path / "storage", job_id, aspect_ratios=["16:9"], n_scenes=2)

    monkeypatch.setattr(job_runner, "primary_footage_provider", MockFootageProvider())
    monkeypatch.setattr(job_runner, "secondary_footage_provider", MockFootageProvider())
    monkeypatch.setattr(job_runner, "fallback_ai_provider", MockFootageProvider())

    def broken_render(scene_id, *args, **kwargs):
        # Write a dummy non-playable file
        output_path = kwargs.get("output_path") or args[4]
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"\x00" * 100)
        return Path(output_path)

    monkeypatch.setattr(ffmpeg_engine, "render_scene_clip", broken_render)

    req = SceneClipReplacementRequest()
    with pytest.raises(RenderFailedError):
        await swapper.replace_scene_clip(job_id, "scene_000", req)


# ---------------------------------------------------------------------------
# 12. Failed replacement does not corrupt the previous valid output
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failed_swap_does_not_corrupt_previous_output(tmp_path, monkeypatch, swapper):
    """When render fails, the existing final video must remain intact on disk."""
    if not ffmpeg_engine.is_available():
        pytest.skip("FFmpeg binary not available")

    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    job_id = "swap-test-no-corrupt-012"
    _build_completed_job(tmp_path / "storage", job_id, aspect_ratios=["16:9"], n_scenes=2)

    job_dir = tmp_path / "storage" / job_id
    original_video = job_dir / "video_16_9.mp4"
    original_size = original_video.stat().st_size
    original_mtime = original_video.stat().st_mtime

    monkeypatch.setattr(job_runner, "primary_footage_provider", MockFootageProvider())
    monkeypatch.setattr(job_runner, "secondary_footage_provider", MockFootageProvider())
    monkeypatch.setattr(job_runner, "fallback_ai_provider", MockFootageProvider())

    # Make render fail partway through
    def broken_render(scene_id, *args, **kwargs):
        raise RenderFailedError(f"Simulated FFmpeg crash for {scene_id}")

    monkeypatch.setattr(ffmpeg_engine, "render_scene_clip", broken_render)

    with pytest.raises(RenderFailedError):
        await swapper.replace_scene_clip(job_id, "scene_000", SceneClipReplacementRequest())

    # Original video must be untouched
    assert original_video.exists(), "Original final video must still exist after failed swap"
    assert original_video.stat().st_size == original_size, "Original video size must not change after failure"


# ---------------------------------------------------------------------------
# 13. Temporary staging directory is cleaned up after success and after failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_staging_directory_cleaned_up_on_success(tmp_path, monkeypatch, swapper):
    """After a successful swap, no swap_* staging directory remains under storage/staging/."""
    if not ffmpeg_engine.is_available():
        pytest.skip("FFmpeg binary not available")

    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    job_id = "swap-test-cleanup-success-013a"
    _build_completed_job(tmp_path / "storage", job_id, aspect_ratios=["16:9"], n_scenes=2)

    monkeypatch.setattr(job_runner, "primary_footage_provider", MockFootageProvider())
    monkeypatch.setattr(job_runner, "secondary_footage_provider", MockFootageProvider())
    monkeypatch.setattr(job_runner, "fallback_ai_provider", MockFootageProvider())

    await swapper.replace_scene_clip(job_id, "scene_000", SceneClipReplacementRequest())

    staging_root = tmp_path / "storage" / "staging"
    if staging_root.exists():
        leftovers = list(staging_root.glob(f"swap_{job_id}_*"))
        assert len(leftovers) == 0, f"Staging directories not cleaned up: {leftovers}"


@pytest.mark.asyncio
async def test_staging_directory_cleaned_up_on_failure(tmp_path, monkeypatch, swapper):
    """After a failed swap, no swap_* staging directory remains under storage/staging/."""
    if not ffmpeg_engine.is_available():
        pytest.skip("FFmpeg binary not available")

    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    job_id = "swap-test-cleanup-failure-013b"
    _build_completed_job(tmp_path / "storage", job_id, aspect_ratios=["16:9"], n_scenes=2)

    monkeypatch.setattr(job_runner, "primary_footage_provider", MockFootageProvider())
    monkeypatch.setattr(job_runner, "secondary_footage_provider", MockFootageProvider())
    monkeypatch.setattr(job_runner, "fallback_ai_provider", MockFootageProvider())

    def broken_render(*args, **kwargs):
        raise RenderFailedError("Simulated failure")

    monkeypatch.setattr(ffmpeg_engine, "render_scene_clip", broken_render)

    with pytest.raises(RenderFailedError):
        await swapper.replace_scene_clip(job_id, "scene_000", SceneClipReplacementRequest())

    staging_root = tmp_path / "storage" / "staging"
    if staging_root.exists():
        leftovers = list(staging_root.glob(f"swap_{job_id}_*"))
        assert len(leftovers) == 0, f"Staging directories not cleaned up after failure: {leftovers}"


# ---------------------------------------------------------------------------
# 14. Concurrent requests on the same job are blocked (locking strategy)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_swap_on_same_job_is_blocked(tmp_path, monkeypatch, swapper):
    """A second concurrent swap on the same job_id raises InvalidInputError while lock is held."""
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    job_id = "swap-test-concurrency-014"
    _build_completed_job(tmp_path / "storage", job_id, aspect_ratios=["16:9"], n_scenes=2)

    # Pre-acquire the per-job lock to simulate an in-progress swap
    job_lock = await swapper._get_job_lock(job_id)

    async with job_lock:
        req = SceneClipReplacementRequest()
        with pytest.raises(InvalidInputError) as exc_info:
            await swapper.replace_scene_clip(job_id, "scene_000", req)
        assert "active" in str(exc_info.value).lower() or "locked" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_concurrent_swaps_on_different_jobs_are_allowed(tmp_path, monkeypatch):
    """Two concurrent swap operations on *different* jobs must not block each other."""
    if not ffmpeg_engine.is_available():
        pytest.skip("FFmpeg binary not available")

    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path / "storage"))

    for job_id in ("swap-conc-job-a", "swap-conc-job-b"):
        _build_completed_job(tmp_path / "storage", job_id, aspect_ratios=["16:9"], n_scenes=2)
        monkeypatch.setattr(job_runner, "primary_footage_provider", MockFootageProvider())
        monkeypatch.setattr(job_runner, "secondary_footage_provider", MockFootageProvider())
        monkeypatch.setattr(job_runner, "fallback_ai_provider", MockFootageProvider())

    swapper_a = SceneSwapper()
    swapper_b = SceneSwapper()

    results = await asyncio.gather(
        swapper_a.replace_scene_clip("swap-conc-job-a", "scene_000", SceneClipReplacementRequest()),
        swapper_b.replace_scene_clip("swap-conc-job-b", "scene_000", SceneClipReplacementRequest()),
    )
    assert results[0]["status"] == "completed"
    assert results[1]["status"] == "completed"


# ---------------------------------------------------------------------------
# 15. Custom video_url — validation and unsafe scheme rejection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("bad_url", [
    "file:///etc/passwd",
    "../local/path.mp4",
    "file://C:/Windows/System32/cmd.exe",
])
async def test_unsafe_video_url_schemes_rejected(bad_url, tmp_path, monkeypatch, swapper):
    """Unsafe or local video_url values must raise InvalidInputError."""
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    job_id = "swap-test-bad-url-015"
    _build_completed_job(tmp_path / "storage", job_id, aspect_ratios=["16:9"], n_scenes=2)

    req = SceneClipReplacementRequest(video_url=bad_url)
    with pytest.raises(InvalidInputError):
        await swapper.replace_scene_clip(job_id, "scene_000", req)


@pytest.mark.asyncio
async def test_video_url_download_failure_raises_invalid_input(tmp_path, monkeypatch, swapper):
    """When video_url download fails, InvalidInputError is raised."""
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    job_id = "swap-test-url-fail-015b"
    _build_completed_job(tmp_path / "storage", job_id, aspect_ratios=["16:9"], n_scenes=2)

    import httpx
    with patch("httpx.Client.get", side_effect=httpx.ConnectError("connection refused")):
        req = SceneClipReplacementRequest(video_url="https://notexist.invalid/clip.mp4")
        with pytest.raises(InvalidInputError) as exc_info:
            await swapper.replace_scene_clip(job_id, "scene_000", req)
        assert "video_url" in str(exc_info.value).lower() or "download" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 16. Backward-compatible POST /swap endpoint via FastAPI TestClient
# ---------------------------------------------------------------------------

def test_swap_endpoint_missing_job_returns_404(client):
    """POST /swap on a non-existent job returns HTTP 404."""
    resp = client.post(
        "/api/v1/broll/jobs/nonexistent-job-999/scenes/scene_000/swap",
        json={},
    )
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"]["code"] == "JOB_NOT_FOUND"


def test_put_clip_endpoint_missing_job_returns_404(client):
    """PUT /clip on a non-existent job returns HTTP 404."""
    resp = client.put(
        "/api/v1/broll/jobs/nonexistent-job-998/scenes/scene_000/clip",
        json={},
    )
    assert resp.status_code == 404


def test_put_clip_endpoint_registered_in_openapi(client):
    """The PUT scenes/{scene_id}/clip endpoint is present in the OpenAPI schema."""
    resp = client.get("/api/v1/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    paths = schema.get("paths", {})
    matching = [p for p in paths if "/scenes/{scene_id}/clip" in p]
    assert len(matching) > 0, "PUT /scenes/{scene_id}/clip not found in OpenAPI spec"


def test_swap_and_put_clip_accept_same_optional_fields(client):
    """Both endpoints accept the same optional request fields without validation errors."""
    payload = {
        "query_override": "mountain landscape",
        "search_description": "scenic alpine view",
        "provider_override": "mock",
        "clip_id": "mock_clip_001",
    }
    # Neither should return 422 (unprocessable entity); both should return 404 (job not found)
    for path in [
        "/api/v1/broll/jobs/no-job-id/scenes/scene_000/swap",
        "/api/v1/broll/jobs/no-job-id/scenes/scene_000/clip",
    ]:
        resp = client.post(path, json=payload) if "swap" in path else client.put(path, json=payload)
        assert resp.status_code in (404, 200), f"Unexpected status {resp.status_code} for {path}"
        assert resp.status_code != 422, f"Endpoint {path} rejected valid optional fields"
