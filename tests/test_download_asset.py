"""Tests for download_asset endpoint covering direct download, inline preview, and fallback retrieval."""
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings

def test_download_asset_attachment_and_preview(tmp_path):
    client = TestClient(app)
    job_id = "test-dl-job-uuid"
    
    # Create storage folder with a test video
    job_storage = Path(settings.LOCAL_STORAGE_DIR) / job_id
    job_storage.mkdir(parents=True, exist_ok=True)
    video_file = job_storage / "video_16_9.mp4"
    video_file.write_bytes(b"sample video bytes content")
    
    try:
        # 1. Test normal attachment download
        res_dl = client.get(f"/api/v1/jobs/download/{job_id}/video_16_9.mp4")
        assert res_dl.status_code == 200
        assert res_dl.content == b"sample video bytes content"
        assert 'attachment; filename="video_16_9.mp4"' in res_dl.headers.get("content-disposition", "")
        assert res_dl.headers.get("accept-ranges") == "bytes"
        
        # 2. Test inline video preview (?preview=1)
        res_prev = client.get(f"/api/v1/jobs/download/{job_id}/video_16_9.mp4?preview=1")
        assert res_prev.status_code == 200
        assert res_prev.content == b"sample video bytes content"
        assert res_prev.headers.get("content-disposition") == "inline"
        
        # 3. Test non-existent file returns 404
        res_404 = client.get(f"/api/v1/jobs/download/{job_id}/missing.mp4")
        assert res_404.status_code == 404
        assert res_404.json()["error"]["code"] == "JOB_NOT_FOUND"
    finally:
        import shutil
        shutil.rmtree(job_storage, ignore_errors=True)
