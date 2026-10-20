"""Tests for video artifact download headers and content-disposition."""
from pathlib import Path
from app.core.config import settings


def test_video_download_attachment_headers(client, tmp_path):
    # Setup dummy video file in local storage dir
    job_id = "test-job-download"
    job_dir = Path(settings.LOCAL_STORAGE_DIR) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    video_file = job_dir / "video_16_9.mp4"
    video_file.write_bytes(b"\x00\x00\x00 ftypisom\x00\x00\x02\x00isomiso2mp41")

    try:
        # Test 1: Download via filename
        resp1 = client.get(f"/api/v1/jobs/download/{job_id}/video_16_9.mp4")
        assert resp1.status_code == 200
        assert resp1.headers["content-type"] == "video/mp4"
        assert 'attachment; filename="video_16_9.mp4"' in resp1.headers.get("content-disposition", "")

        # Test 2: Download via asset_key
        resp2 = client.get(f"/api/v1/jobs/download/{job_id}/video_16_9")
        assert resp2.status_code == 200
        assert resp2.headers["content-type"] == "video/mp4"
        assert 'attachment; filename="video_16_9.mp4"' in resp2.headers.get("content-disposition", "")

        # Test 3: Download via artifact alias
        resp3 = client.get(f"/api/v1/jobs/{job_id}/artifacts/video_16_9")
        assert resp3.status_code == 200
        assert resp3.headers["content-type"] == "video/mp4"
        assert 'attachment; filename="video_16_9.mp4"' in resp3.headers.get("content-disposition", "")
    finally:
        if video_file.exists():
            video_file.unlink()
        if job_dir.exists():
            job_dir.rmdir()
