"""Unit tests for Media Caching, Job Isolation, and Workspace Hygiene."""
import time
from pathlib import Path
import pytest

from app.core.config import settings
from app.core.database import db
from app.core.exceptions import RenderFailedError
from app.rendering.ffmpeg_engine import ffmpeg_engine
from app.services.footage_provider import footage_provider_service
from app.workers.job_runner import job_runner


def test_cross_job_media_cache_isolation(tmp_path):
    """Enforce that cached footage paths are strictly partitioned by job_id."""
    fake_clip = tmp_path / "clip_alpha.mp4"
    fake_clip.write_bytes(b"dummy_video_bytes_123456")

    provider = "pixabay"
    query = "running athlete in morning light"

    # Store for job-101
    footage_provider_service.store_cached_clip(provider, query, "job-101", fake_clip)

    # Retrieval with job-101 succeeds
    cached_for_101 = footage_provider_service.get_cached_clip(provider, query, "job-101")
    assert cached_for_101 == fake_clip

    # Retrieval for an unrelated job-202 MUST return None
    cached_for_202 = footage_provider_service.get_cached_clip(provider, query, "job-202")
    assert cached_for_202 is None

    # Clear cache for job-101
    footage_provider_service.clear_job_cache("job-101")
    assert footage_provider_service.get_cached_clip(provider, query, "job-101") is None


def test_stale_clip_mtime_rejection(tmp_path):
    """Clips predating current job execution window must be rejected as stale."""
    stale_clip = tmp_path / "stale_clip.mp4"
    stale_clip.write_bytes(b"stale_video_payload")

    # Set mtime back by 60 seconds
    past_time = time.time() - 60.0
    import os
    os.utime(stale_clip, (past_time, past_time))

    job_start_time = time.time()

    is_fresh = footage_provider_service.verify_input_clip_freshness(
        stale_clip,
        job_start_time=job_start_time,
        job_dir=tmp_path,
    )
    assert is_fresh is False


def test_cross_job_directory_containment(tmp_path):
    """Clips located outside the designated job_dir must be rejected."""
    job_dir = tmp_path / "job_run_fresh"
    job_dir.mkdir(parents=True, exist_ok=True)

    outside_dir = tmp_path / "unrelated_job_stale"
    outside_dir.mkdir(parents=True, exist_ok=True)

    outside_clip = outside_dir / "leaked_clip.mp4"
    outside_clip.write_bytes(b"leaked_media")

    is_valid = footage_provider_service.verify_input_clip_freshness(
        outside_clip,
        job_start_time=time.time(),
        job_dir=job_dir,
    )
    assert is_valid is False


def test_concat_validation_rejects_stale_inputs(tmp_path):
    """Before writing concat_list.txt, ffmpeg_engine must verify input clips were created during current run."""
    staging = tmp_path / "staging_concat_test"
    scenes_dir = staging / "scenes_16_9"
    scenes_dir.mkdir(parents=True, exist_ok=True)

    clip0 = scenes_dir / "clip_000.mp4"
    clip0.write_bytes(b"dummy")

    # Set clip0 mtime into past
    old_time = time.time() - 50.0
    import os
    os.utime(clip0, (old_time, old_time))

    scenes_data = [{"scene_id": "sc1", "text": "test", "duration": 2.0}]

    with pytest.raises(RenderFailedError, match="was not created during the current job run"):
        ffmpeg_engine.assemble_video(
            scenes_data=scenes_data,
            aspect_ratio="16:9",
            narration_audio_path=None,
            output_path=staging / "out.mp4",
            staging_dir=staging,
            reuse_existing_clips=True,
            job_start_time=time.time(),
        )


@pytest.mark.asyncio
async def test_job_runner_workspace_hygiene_removes_scratch_files():
    """Job completion must cleanly remove scratch files (downloads, scenes_*, concat_list.txt) leaving only final exports."""
    job_id = "test-job-hygiene-cleanup-001"
    req_payload = {
        "script": "A peaceful mountain lake surrounded by dense evergreen trees.",
        "aspect_ratios": ["16:9"],
        "voice": "en-IN-female",
    }
    db.create_job(job_id, req_payload)

    await job_runner.execute_job(job_id)

    record = db.get_job(job_id)
    assert record["status"] == "completed"

    final_storage = Path(settings.LOCAL_STORAGE_DIR) / job_id
    assert final_storage.exists()

    # Final exported assets MUST exist
    assert (final_storage / "video_16_9.mp4").exists()
    assert (final_storage / "manifest.json").exists()
    assert (final_storage / "timeline.json").exists()
    assert (final_storage / "captions.srt").exists()

    # Intermediate scratch files MUST have been cleaned up
    assert not (final_storage / "downloads").exists()
    assert not (final_storage / "scenes_16_9").exists()
    assert not (final_storage / "concat_16_9.txt").exists()
    assert not (final_storage / "concat_list.txt").exists()


@pytest.mark.asyncio
async def test_job_runner_no_repeating_clips_across_scenes():
    """Verify that multi-scene scripts never repeat visual clips across scenes in the same job."""
    job_id = "test-job-no-repeating-clips-001"
    req_payload = {
        "aspect_ratios": ["16:9"],
        "voice": "en-IN-female",
        "scenes": [
            {"text": "Master craftsmen build mechanical watches with precision.", "visual_hint": "watch gears mechanical cogs"},
            {"text": "Every microscopic balance wheel oscillates steadily.", "visual_hint": "watch gears mechanical cogs"},
            {"text": "Artisans assemble delicate sapphire dials.", "visual_hint": "watch gears mechanical cogs"},
            {"text": "Timeless mechanical engineering endures through centuries.", "visual_hint": "watch gears mechanical cogs"},
        ],
    }
    db.create_job(job_id, req_payload)

    await job_runner.execute_job(job_id)

    record = db.get_job(job_id)
    assert record["status"] == "completed"

    scenes = record["timeline"]["scenes"]
    assert len(scenes) == 4

    clip_ids = [sc["clip"]["clip_id"] for sc in scenes]
    assert len(set(clip_ids)) == len(clip_ids), f"Duplicate clip_ids detected across scenes: {clip_ids}"
