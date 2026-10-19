"""Automated Tests for Phase 3A: Real FFmpeg Video Assembly Engine (Requirement MH-4)."""
from pathlib import Path
import pytest
from app.core.config import settings
from app.core.database import db
from app.rendering.ffmpeg_engine import ffmpeg_engine, probe_media
from app.workers.job_runner import job_runner


def test_ffmpeg_binary_availability():
    """Verify that FFmpeg executable is discovered and operational."""
    assert ffmpeg_engine.is_available(), f"FFmpeg binary not accessible at {settings.FFMPEG_PATH}"


def test_dummy_mp4_header_fails_probe(tmp_path):
    """Negative test: Probing a dummy 2080-byte MP4 header (the old mock output) must fail validation."""
    dummy_file = tmp_path / "dummy_video.mp4"
    ftyp_header = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2mp41\x00\x00\x00\x08free"
    media_payload = b"\x00" * 2048
    dummy_file.write_bytes(ftyp_header + media_payload)

    info = probe_media(dummy_file)
    assert info["is_valid"] is False, "Dummy MP4 header should NOT be reported as valid"
    assert info["has_video"] is False, "Dummy MP4 header must not have video stream"
    assert "moov" in info.get("error", "").lower() or "invalid" in info.get("error", "").lower()


def test_render_scene_clip_dimensions_and_fps(tmp_path):
    """Verify individual scene clip generation for both 16:9 and 9:16 aspect ratios."""
    if not ffmpeg_engine.is_available():
        pytest.skip("FFmpeg binary not available on host system")

    # 16:9 scene clip
    clip_16_9 = tmp_path / "scene_16_9.mp4"
    ffmpeg_engine.render_scene_clip(
        scene_id="test_sc_01",
        scene_text="IncuBrix 16:9 Scene Test",
        duration=1.2,
        aspect_ratio="16:9",
        output_path=clip_16_9,
        scene_index=0,
    )
    assert clip_16_9.exists()
    assert clip_16_9.stat().st_size > 2000

    probe_16_9 = probe_media(clip_16_9)
    assert probe_16_9["is_valid"] is True
    assert probe_16_9["has_video"] is True
    assert probe_16_9["video_codec"] == "h264"
    assert probe_16_9["resolution"] == "1920x1080"
    assert probe_16_9["fps"] == 30.0
    assert probe_16_9["duration"] >= 1.0

    # 9:16 scene clip
    clip_9_16 = tmp_path / "scene_9_16.mp4"
    ffmpeg_engine.render_scene_clip(
        scene_id="test_sc_02",
        scene_text="IncuBrix 9:16 Vertical Reel Test",
        duration=1.2,
        aspect_ratio="9:16",
        output_path=clip_9_16,
        scene_index=1,
    )
    assert clip_9_16.exists()
    assert clip_9_16.stat().st_size > 2000

    probe_9_16 = probe_media(clip_9_16)
    assert probe_9_16["is_valid"] is True
    assert probe_9_16["has_video"] is True
    assert probe_9_16["video_codec"] == "h264"
    assert probe_9_16["resolution"] == "1080x1920"
    assert probe_9_16["fps"] == 30.0


@pytest.mark.asyncio
async def test_full_ffmpeg_pipeline_generates_playable_videos_and_audio():
    """End-to-end integration test: Full job execution generates playable 16:9 and 9:16 MP4s with audio."""
    if not ffmpeg_engine.is_available():
        pytest.skip("FFmpeg binary not available on host system")

    job_id = "test-ffmpeg-playable-001"
    req_payload = {
        "script": "A creator records a sixty-second voiceover. Building valuable video assets.",
        "aspect_ratios": ["16:9", "9:16"],
        "voice": "en-IN-female",
    }
    db.create_job(job_id, req_payload)

    # Execute full pipeline
    await job_runner.execute_job(job_id)

    record = db.get_job(job_id)
    assert record["status"] == "completed"
    assert record["progress"] == 100

    storage_dir = Path(settings.LOCAL_STORAGE_DIR) / job_id
    video_16_9 = storage_dir / "video_16_9.mp4"
    video_9_16 = storage_dir / "video_9_16.mp4"
    narration_audio = storage_dir / "narration.wav"
    captions = storage_dir / "captions.srt"
    timeline = storage_dir / "timeline.json"
    manifest = storage_dir / "manifest.json"

    # Verify all output files exist
    assert video_16_9.exists()
    assert video_9_16.exists()
    assert narration_audio.exists()
    assert captions.exists()
    assert timeline.exists()
    assert manifest.exists()

    # 1. Probe video_16_9.mp4
    p16 = probe_media(video_16_9)
    assert p16["is_valid"] is True, f"video_16_9 is invalid: {p16.get('error')}"
    assert p16["has_video"] is True, "video_16_9 must have video stream"
    assert p16["has_audio"] is True, "video_16_9 must have muxed audio stream"
    assert p16["video_codec"] == "h264", f"Expected h264, got {p16.get('video_codec')}"
    assert p16["audio_codec"] == "aac", f"Expected aac audio, got {p16.get('audio_codec')}"
    assert p16["resolution"] == "1920x1080", f"Expected 1920x1080, got {p16.get('resolution')}"
    assert p16["fps"] == 30.0, f"Expected 30 fps, got {p16.get('fps')}"
    assert p16["duration"] > 0.0
    assert p16["file_size_bytes"] > 5000, f"Video size too small: {p16['file_size_bytes']} bytes"

    # 2. Probe video_9_16.mp4
    p9 = probe_media(video_9_16)
    assert p9["is_valid"] is True, f"video_9_16 is invalid: {p9.get('error')}"
    assert p9["has_video"] is True, "video_9_16 must have video stream"
    assert p9["has_audio"] is True, "video_9_16 must have muxed audio stream"
    assert p9["video_codec"] == "h264"
    assert p9["audio_codec"] == "aac"
    assert p9["resolution"] == "1080x1920", f"Expected 1080x1920, got {p9.get('resolution')}"
    assert p9["fps"] == 30.0
    assert p9["duration"] > 0.0
    assert p9["file_size_bytes"] > 5000


@pytest.mark.asyncio
async def test_assemble_video_burned_in_subtitles_16_9_and_9_16(tmp_path):
    """Verify that assemble_video properly burns in subtitles for 16:9 and 9:16 aspect ratios."""
    if not ffmpeg_engine.is_available():
        pytest.skip("FFmpeg not available")

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    # Create dummy scene data
    scenes_data = [
        {"scene_id": "sc_001", "text": "First scene caption text.", "duration": 1.5, "raw_clip_path": None},
        {"scene_id": "sc_002", "text": "Second scene caption text.", "duration": 1.5, "raw_clip_path": None},
    ]

    # Create sample SRT file
    srt_file = staging_dir / "captions.srt"
    srt_file.write_text(
        "1\n00:00:00,000 --> 00:00:01,500\nFirst scene caption text.\n\n"
        "2\n00:00:01,500 --> 00:00:03,000\nSecond scene caption text.\n",
        encoding="utf-8",
    )

    # 1. Assemble 16:9 with burn_in=True
    out_16_9 = staging_dir / "out_16_9.mp4"
    res_16 = ffmpeg_engine.assemble_video(
        scenes_data=scenes_data,
        aspect_ratio="16:9",
        narration_audio_path=None,
        output_path=out_16_9,
        staging_dir=staging_dir,
        captions_srt_path=srt_file,
        burn_in=True,
    )
    assert res_16.exists()
    p16 = probe_media(res_16)
    assert p16["is_valid"] is True
    assert p16["resolution"] == "1920x1080"
    assert p16["video_codec"] == "h264"
    assert p16["has_audio"] is True

    # 2. Assemble 9:16 with burn_in=True
    out_9_16 = staging_dir / "out_9_16.mp4"
    res_9 = ffmpeg_engine.assemble_video(
        scenes_data=scenes_data,
        aspect_ratio="9:16",
        narration_audio_path=None,
        output_path=out_9_16,
        staging_dir=staging_dir,
        captions_srt_path=srt_file,
        burn_in=True,
    )
    assert res_9.exists()
    p9 = probe_media(res_9)
    assert p9["is_valid"] is True
    assert p9["resolution"] == "1080x1920"
    assert p9["video_codec"] == "h264"
    assert p9["has_audio"] is True

    # 3. Assemble with burn_in=False (fast stream copy path)
    out_no_burn = staging_dir / "out_no_burn.mp4"
    res_noburn = ffmpeg_engine.assemble_video(
        scenes_data=scenes_data,
        aspect_ratio="16:9",
        narration_audio_path=None,
        output_path=out_no_burn,
        staging_dir=staging_dir,
        captions_srt_path=srt_file,
        burn_in=False,
    )
    assert res_noburn.exists()
    p_noburn = probe_media(res_noburn)
    assert p_noburn["is_valid"] is True


@pytest.mark.asyncio
async def test_full_pipeline_burn_in_toggle():
    """Verify full job pipeline respects captions.burn_in toggle."""
    if not ffmpeg_engine.is_available():
        pytest.skip("FFmpeg not available")

    # Test with explicit burn_in=False
    job_id = "test-captions-noburn-001"
    req_payload = {
        "script": "Testing disabling burned in captions. Fast assembly is preserved.",
        "aspect_ratios": ["16:9"],
        "voice": "en-IN-female",
        "captions": {"burn_in": False, "srt": True},
    }
    db.create_job(job_id, req_payload)
    await job_runner.execute_job(job_id)

    record = db.get_job(job_id)
    assert record["status"] == "completed"

    storage_dir = Path(settings.LOCAL_STORAGE_DIR) / job_id
    assert (storage_dir / "video_16_9.mp4").exists()
    assert (storage_dir / "captions.srt").exists()

    p = probe_media(storage_dir / "video_16_9.mp4")
    assert p["is_valid"] is True

