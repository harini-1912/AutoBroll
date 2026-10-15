"""Tests for Jobs API endpoints, background execution, and inputs."""
import io
import time

def test_create_job_with_script_returns_202(client):
    payload = {
        "script": "A creator records a sixty second voiceover about saving money.",
        "voice": "en-IN-female",
        "aspect_ratios": ["16:9", "9:16"],
        "style": "bright, modern, real-world footage",
    }
    response = client.post("/api/v1/broll/jobs", json=payload)
    assert response.status_code == 202
    data = response.json()
    assert "job_id" in data
    assert data["status"] in ["queued", "running", "succeeded"]
    assert data["stage"] in ["queued", "transcription", "completed"]

def test_create_job_with_scenes_list_returns_202(client):
    payload = {
        "scenes": [
            {"text": "Trust is earned slowly.", "visual_hint": "handshake or solid foundation"},
            {"text": "Sales grew forty percent.", "visual_hint": "upward graph dashboard"},
        ],
        "aspect_ratios": ["1:1"],
    }
    response = client.post("/api/v1/broll/jobs", json=payload)
    assert response.status_code == 202
    data = response.json()
    assert "job_id" in data

def test_create_job_with_audio_url_returns_202(client):
    payload = {
        "audio_url": "https://images.incubrix.test/audio/sample_voiceover.mp3",
        "aspect_ratios": ["16:9"],
    }
    response = client.post("/api/v1/broll/jobs", json=payload)
    assert response.status_code == 202
    data = response.json()
    assert "job_id" in data

def test_create_job_with_audio_upload_returns_202(client):
    fake_audio = io.BytesIO(b"FAKE_MP3_AUDIO_HEADER_DATA_STREAM")
    files = {"audio_file": ("voiceover.mp3", fake_audio, "audio/mpeg")}
    data = {"voice": "en-IN-female", "aspect_ratios": "16:9,9:16"}

    response = client.post("/api/v1/jobs/upload", files=files, data=data)
    assert response.status_code == 202
    resp_data = response.json()
    assert "job_id" in resp_data

def test_create_job_with_invalid_audio_extension_returns_415(client):
    fake_file = io.BytesIO(b"SOMETHING_NOT_AUDIO")
    files = {"audio_file": ("document.pdf", fake_file, "application/pdf")}

    response = client.post("/api/v1/jobs/upload", files=files, data={})
    assert response.status_code == 415
    data = response.json()
    assert data["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"

def test_get_job_polling_and_completion(client):
    # Submit job
    payload = {"script": "Testing job completion and result URLs."}
    res = client.post("/api/v1/broll/jobs", json=payload)
    job_id = res.json()["job_id"]

    # Poll status
    time.sleep(0.3)  # wait for mock background execution
    poll_res = client.get(f"/api/v1/jobs/{job_id}")
    assert poll_res.status_code == 200
    job_status = poll_res.json()
    assert job_status["status"] in ["processing", "running", "completed", "succeeded"]
    if job_status["status"] in ["completed", "succeeded"]:
        assert job_status["progress"] == 100
        assert "result_urls" in job_status
        assert "captions_srt" in job_status["result_urls"]
