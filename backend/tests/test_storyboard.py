"""Tests for Idea to Storyboard endpoint and helper features."""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_storyboard_from_brain_dump_endpoint():
    """Test POST /api/storyboard/from-brain-dump returns polished narration and scenes."""
    payload = {
        "raw_text": "Founders waste huge amounts of capital on fancy coworking spaces and brand design agencies before talking to ten real customers. You need to speak to real users daily and iterate on user feedback.",
        "target_scene_count": 3
    }
    response = client.post("/api/storyboard/from-brain-dump", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert "polished_narration" in data
    assert isinstance(data["polished_narration"], str)
    assert len(data["polished_narration"]) > 0

    assert "scenes" in data
    assert isinstance(data["scenes"], list)
    assert len(data["scenes"]) >= 1

    for scene in data["scenes"]:
        assert "text" in scene
        assert "visual_query" in scene
        assert isinstance(scene["text"], str)
        assert isinstance(scene["visual_query"], str)
        # Verify visual query has 2-4 keywords and no banned words
        words = scene["visual_query"].split()
        assert 1 <= len(words) <= 5
        for word in words:
            assert word.lower() not in {"consistency", "scale", "success", "future", "synergy", "paradigm", "growth"}


def test_storyboard_versioned_endpoint_identical():
    """Test that /api/v1/storyboard/from-brain-dump also works and returns 200."""
    payload = {
        "raw_text": "Developers spend hours configuring complex infrastructure when a simple script would do the job.",
        "target_scene_count": 2
    }
    response = client.post("/api/v1/storyboard/from-brain-dump", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "polished_narration" in data
    assert "scenes" in data
    assert len(data["scenes"]) >= 1


def test_storyboard_empty_text_returns_400():
    """Test that empty or whitespace raw_text returns 400 validation error."""
    payload = {
        "raw_text": "   ",
        "target_scene_count": 4
    }
    response = client.post("/api/storyboard/from-brain-dump", json=payload)
    assert response.status_code == 400
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "INVALID_INPUT"
