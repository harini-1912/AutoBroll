"""Tests for timeline and manifest inspection endpoints."""
from app.core.database import db

def test_timeline_and_manifest_endpoints_for_completed_job(client):
    job_id = "job-metadata-check-001"
    req = {"script": "Checking metadata endpoints."}
    db.create_job(job_id, req)

    # Complete job with sample timeline and manifest
    timeline = {"job_id": job_id, "total_scenes": 1, "scenes": [{"scene_id": "s1", "text": "hello"}]}
    manifest = {"job_id": job_id, "total_usd": 0.001, "models": []}
    db.complete_job_success(job_id, {}, timeline, manifest)

    # Check GET timeline
    res_timeline = client.get(f"/api/v1/broll/jobs/{job_id}/timeline")
    assert res_timeline.status_code == 200
    assert res_timeline.json()["job_id"] == job_id
    assert res_timeline.json()["total_scenes"] == 1

    # Check GET manifest
    res_manifest = client.get(f"/api/v1/broll/jobs/{job_id}/manifest")
    assert res_manifest.status_code == 200
    assert res_manifest.json()["job_id"] == job_id

def test_timeline_endpoint_job_not_found(client):
    res = client.get("/api/v1/broll/jobs/missing-job-xyz/timeline")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "JOB_NOT_FOUND"

def test_manifest_endpoint_job_not_found(client):
    res = client.get("/api/v1/broll/jobs/missing-job-xyz/manifest")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "JOB_NOT_FOUND"
