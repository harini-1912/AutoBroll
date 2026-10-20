"""Tests for standardized error handling conforming to Integration Standard #5."""

def test_missing_all_inputs_returns_standard_error(client):
    response = client.post("/api/v1/broll/jobs", json={})
    assert response.status_code == 400
    data = response.json()
    assert "error" in data
    error = data["error"]
    assert error["code"] == "INVALID_INPUT"
    assert "message" in error
    assert error["retryable"] is False
    assert "details" in error

def test_unknown_job_id_returns_404(client):
    response = client.get("/api/v1/jobs/non-existent-uuid-999")
    assert response.status_code == 404
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "JOB_NOT_FOUND"
    assert data["error"]["retryable"] is False

def test_invalid_aspect_ratio_returns_400(client):
    payload = {
        "script": "Testing invalid aspect ratios.",
        "aspect_ratios": ["4:3", "invalid_ratio"],
    }
    response = client.post("/api/v1/broll/jobs", json=payload)
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "INVALID_INPUT"


def test_asset_not_found_error_structure():
    from app.core.exceptions import AssetNotFoundError
    err = AssetNotFoundError("AssetNotFound: Could not find required file at /tmp/test/clip.mp4")
    assert err.code == "ASSET_NOT_FOUND"
    assert err.status_code == 404
    d = err.to_dict()
    assert d["error"]["code"] == "ASSET_NOT_FOUND"
    assert "/tmp/test/clip.mp4" in d["error"]["message"]

