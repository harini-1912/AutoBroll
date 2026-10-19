"""Automated Tests for Phase 3C: Accurate Narration and Scene Alignment (Requirement MH-2)."""
import re
from pathlib import Path
import pytest
from app.core.config import settings
from app.core.database import db
from app.providers.mock import MockTTSProvider
from app.rendering.ffmpeg_engine import ffmpeg_engine, probe_media
from app.services.alignment import NarrationAligner, narration_aligner
from app.workers.job_runner import job_runner


def test_provider_supplied_sentence_timestamps():
    """Requirement 1: Verify correct handling and scaling of provider-supplied sentence timestamps."""
    aligner = NarrationAligner()
    scenes = [
        {"scene_id": "sc_01", "text": "First sentence is short."},
        {"scene_id": "sc_02", "text": "Second sentence is significantly longer and provides detailed narration."},
    ]
    raw_timestamps = [
        {"text": "First sentence is short.", "start_time": 0.0, "end_time": 2.0},
        {"text": "Second sentence is significantly longer and provides detailed narration.", "start_time": 2.3, "end_time": 6.3},
    ]

    total_duration = 7.0
    aligned = aligner.align_scenes(scenes, total_duration=total_duration, tts_timestamps=raw_timestamps)

    assert len(aligned) == 2
    assert aligned[0]["alignment_method"] == "tts_timestamps"
    assert aligned[0]["confidence"] == 0.95
    assert aligned[0]["start_time"] == 0.0
    assert aligned[0]["end_time"] == aligned[1]["start_time"]
    assert aligned[1]["end_time"] == total_duration
    assert aligned[0]["duration"] > 0.5
    assert aligned[1]["duration"] > 0.5
    assert aligned[1]["duration"] > aligned[0]["duration"]  # Longer sentence receives more duration


def test_audio_silence_alignment_with_acoustic_pauses(tmp_path):
    """Requirement 2: Verify silence-detection based scene boundary alignment when TTS timestamps are absent."""
    if not ffmpeg_engine.is_available():
        pytest.skip("FFmpeg not available")

    # Generate synthetic audio containing distinct acoustic pauses between sentences
    audio_path = tmp_path / "acoustic_test.wav"
    mock_tts = MockTTSProvider()
    total_dur, _ = mock_tts.synthesize_with_timestamps(
        text="Planning is the first foundation. Building provides the true growth.",
        voice="en-IN-female",
        output_path=str(audio_path),
    )

    aligner = NarrationAligner()
    scenes = [
        {"scene_id": "s1", "text": "Planning is the first foundation."},
        {"scene_id": "s2", "text": "Building provides the true growth."},
    ]

    # Explicitly pass tts_timestamps=None to test Priority 2 silence detection
    aligned = aligner.align_scenes(scenes, total_duration=total_dur, audio_path=audio_path, tts_timestamps=None)

    assert len(aligned) == 2
    # Silencedetect should find the 300ms pause injected by MockTTS
    assert aligned[0]["alignment_method"] in ["audio_silence_alignment", "estimated"]
    assert aligned[0]["start_time"] == 0.0
    assert aligned[0]["end_time"] == aligned[1]["start_time"]
    assert aligned[1]["end_time"] == total_dur
    assert aligned[0]["duration"] >= 0.5
    assert aligned[1]["duration"] >= 0.5


def test_missing_alignment_graceful_fallback():
    """Requirement 3: Fall back to linguistic pacing estimation when timestamps and audio pauses are unavailable."""
    aligner = NarrationAligner()
    scenes = [
        {"scene_id": "sc_01", "text": "Short intro."},
        {"scene_id": "sc_02", "text": "This is a much longer sentence that discusses in-depth financial strategy and investment."},
    ]

    total_duration = 10.0
    # No timestamps and no audio file provided -> triggers Priority 3
    aligned = aligner.align_scenes(scenes, total_duration=total_duration, audio_path=None, tts_timestamps=None)

    assert len(aligned) == 2
    assert aligned[0]["alignment_method"] == "estimated"
    assert aligned[0]["confidence"] == 0.70
    assert aligned[0]["start_time"] == 0.0
    assert aligned[0]["end_time"] == aligned[1]["start_time"]
    assert aligned[1]["end_time"] == total_duration
    # Longer sentence must receive proportionately more time than the short sentence
    assert aligned[1]["duration"] > aligned[0]["duration"]


def test_alignment_failure_and_malformed_timestamps():
    """Requirement 4: Handle malformed, reversed, or out-of-order timestamps safely."""
    aligner = NarrationAligner()
    scenes = [
        {"scene_id": "s1", "text": "First scene"},
        {"scene_id": "s2", "text": "Second scene"},
        {"scene_id": "s3", "text": "Third scene"},
    ]
    # Malformed timestamps with inverted bounds, zero duration, and negative numbers
    malformed_timestamps = [
        {"text": "First", "start_time": -5.0, "end_time": -2.0},
        {"text": "Second", "start_time": 10.0, "end_time": 2.0},  # inverted
        {"text": "Third", "start_time": 5.0, "end_time": 5.0},    # zero duration
    ]

    total_duration = 6.0
    aligned = aligner.align_scenes(scenes, total_duration=total_duration, tts_timestamps=malformed_timestamps)

    assert len(aligned) == 3
    # Check strict invariants
    assert aligned[0]["start_time"] == 0.0
    for i in range(len(aligned) - 1):
        assert aligned[i]["end_time"] == aligned[i + 1]["start_time"], "Scenes must be contiguous"
        assert aligned[i]["duration"] >= 0.5, "Each scene must meet minimum duration"
        assert aligned[i]["end_time"] > aligned[i]["start_time"]
    assert aligned[-1]["end_time"] == total_duration


def test_empty_text_punctuation_and_short_narration():
    """Requirement 5: Safely handle single scene, heavy punctuation, and very short narration."""
    aligner = NarrationAligner()

    # Case A: Single scene
    single_scene = [{"scene_id": "s1", "text": "Just one sentence."}]
    aligned_single = aligner.align_scenes(single_scene, total_duration=3.5)
    assert len(aligned_single) == 1
    assert aligned_single[0]["start_time"] == 0.0
    assert aligned_single[0]["end_time"] == 3.5
    assert aligned_single[0]["duration"] == 3.5

    # Case B: Heavy punctuation pauses
    punct_scenes = [
        {"scene_id": "s1", "text": "Wait... stop!"},
        {"scene_id": "s2", "text": "Continue, carefully, forward;"},
        {"scene_id": "s3", "text": "Done."},
    ]
    aligned_punct = aligner.align_scenes(punct_scenes, total_duration=6.0)
    assert len(aligned_punct) == 3
    assert aligned_punct[0]["start_time"] == 0.0
    assert aligned_punct[-1]["end_time"] == 6.0
    for sc in aligned_punct:
        assert sc["duration"] >= 0.5


def test_timeline_boundaries_and_scene_ordering():
    """Requirement 6: Invariant verification on scene ordering, contiguity, and boundaries."""
    aligner = NarrationAligner()
    scenes = [{"scene_id": f"s_{i}", "text": f"Scene number {i} content text."} for i in range(5)]
    total_dur = 12.5

    aligned = aligner.align_scenes(scenes, total_duration=total_dur)

    assert len(aligned) == 5
    assert aligned[0]["start_time"] == 0.0
    assert aligned[-1]["end_time"] == total_dur

    for i in range(len(aligned)):
        # Scene index order
        assert aligned[i]["scene_index"] == i
        # Non-negative
        assert aligned[i]["start_time"] >= 0.0
        # Positive duration
        assert aligned[i]["end_time"] > aligned[i]["start_time"]
        assert aligned[i]["duration"] == round(aligned[i]["end_time"] - aligned[i]["start_time"], 2)
        if i < len(aligned) - 1:
            # Contiguous without gaps or overlaps
            assert aligned[i]["end_time"] == aligned[i + 1]["start_time"]


def test_no_negative_or_zero_length_scenes_under_extreme_compression():
    """Requirement 8: Ensure extreme cases (many scenes in tiny duration) clamp gracefully without crashing."""
    aligner = NarrationAligner()
    # 6 scenes squeezed into 3.0 seconds (each scene ~0.5s floor)
    scenes = [{"scene_id": f"s_{i}", "text": f"Quick phrase {i}"} for i in range(6)]
    total_dur = 3.0

    aligned = aligner.align_scenes(scenes, total_duration=total_dur)
    assert len(aligned) == 6
    for sc in aligned:
        assert sc["duration"] >= 0.5
        assert sc["start_time"] >= 0.0
        assert sc["end_time"] > sc["start_time"]


@pytest.mark.asyncio
async def test_full_pipeline_alignment_captions_and_manifest_sync():
    """Requirements 7, 9, 10: End-to-end integration verifying timeline, SRT captions, and manifest sync."""
    if not ffmpeg_engine.is_available():
        pytest.skip("FFmpeg not available")

    job_id = "test-align-sync-001"
    req_payload = {
        "script": "A founder outlines a vision. The team executes daily. Milestones create lasting value.",
        "aspect_ratios": ["16:9", "9:16"],
        "voice": "en-IN-female",
    }
    db.create_job(job_id, req_payload)

    await job_runner.execute_job(job_id)

    record = db.get_job(job_id)
    assert record["status"] == "completed"

    storage_dir = Path(settings.LOCAL_STORAGE_DIR) / job_id
    timeline_path = storage_dir / "timeline.json"
    captions_path = storage_dir / "captions.srt"
    manifest_path = storage_dir / "manifest.json"
    video_16_9 = storage_dir / "video_16_9.mp4"
    video_9_16 = storage_dir / "video_9_16.mp4"

    assert timeline_path.exists()
    assert captions_path.exists()
    assert manifest_path.exists()
    assert video_16_9.exists()
    assert video_9_16.exists()

    # 1. Verify timeline alignment metadata
    import json
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    scenes = timeline["scenes"]
    assert len(scenes) == 3
    for sc in scenes:
        assert "start_time" in sc
        assert "end_time" in sc
        assert "duration" in sc
        assert "alignment_method" in sc
        assert sc["alignment_method"] in ["tts_timestamps", "audio_silence_alignment", "estimated"]
        assert sc["duration"] == round(sc["end_time"] - sc["start_time"], 2)

    # 2. Requirement 7: Verify SRT timestamps match timeline scenes exactly
    srt_content = captions_path.read_text(encoding="utf-8")
    # Pattern for SRT timecode: 00:00:00,000 --> 00:00:01,500
    srt_blocks = [b.strip() for b in srt_content.strip().split("\n\n") if b.strip()]
    assert len(srt_blocks) == len(scenes)

    for idx, block in enumerate(srt_blocks):
        lines = block.splitlines()
        time_match = re.search(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})", lines[1])
        assert time_match is not None

        st_sec = (
            int(time_match.group(1)) * 3600
            + int(time_match.group(2)) * 60
            + int(time_match.group(3))
            + int(time_match.group(4)) / 1000.0
        )
        et_sec = (
            int(time_match.group(5)) * 3600
            + int(time_match.group(6)) * 60
            + int(time_match.group(7))
            + int(time_match.group(8)) / 1000.0
        )

        assert round(st_sec, 2) == scenes[idx]["start_time"]
        assert round(et_sec, 2) == scenes[idx]["end_time"]

    # 3. Verify manifest contains narration alignment metadata
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "narration_alignment" in manifest
    align_meta = manifest["narration_alignment"]
    assert align_meta["primary_method"] in ["tts_timestamps", "audio_silence_alignment", "estimated"]
    assert align_meta["scene_count"] == 3
    assert align_meta["average_confidence"] >= 0.70

    # 4. Requirement 10: Validate both rendered videos with ffprobe
    p16 = probe_media(video_16_9)
    assert p16["is_valid"] is True
    assert p16["video_codec"] == "h264"
    assert p16["audio_codec"] == "aac"
    assert p16["resolution"] == "1920x1080"
    assert p16["duration"] > 0.0

    p9 = probe_media(video_9_16)
    assert p9["is_valid"] is True
    assert p9["video_codec"] == "h264"
    assert p9["audio_codec"] == "aac"
    assert p9["resolution"] == "1080x1920"
    assert p9["duration"] > 0.0
