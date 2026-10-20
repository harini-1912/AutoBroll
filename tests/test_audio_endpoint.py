"""Unit tests for Audio Transcription & Scene Generation Endpoint."""
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_transcribe_audio_file_success(tmp_path):
    fake_audio = tmp_path / "sample.mp3"
    fake_audio.write_bytes(b"dummy mp3 audio content")

    mock_transcript = "Building wealth starts with consistent saving. Financial freedom requires discipline."

    with patch("app.api.v1.endpoints.audio.audio_transcriber.transcribe", return_value=mock_transcript):
        with open(fake_audio, "rb") as f:
            resp = client.post(
                "/api/v1/audio/transcribe-to-scenes",
                files={"file": ("sample.mp3", f, "audio/mpeg")},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert "raw_transcript" in data
    assert data["raw_transcript"] == mock_transcript
    assert "scenes" in data
    assert len(data["scenes"]) == 2
    assert "text" in data["scenes"][0]
    assert "visual_hint" in data["scenes"][0]
    assert data["scenes"][0]["text"].startswith("Building wealth")

def test_transcribe_audio_unsupported_media():
    resp = client.post(
        "/api/v1/audio/transcribe-to-scenes",
        files={"file": ("test.pdf", b"pdf content", "application/pdf")},
    )
    assert resp.status_code == 415
    assert resp.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"

def test_transcribe_audio_missing_inputs():
    resp = client.post("/api/v1/audio/transcribe-to-scenes")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_INPUT"
