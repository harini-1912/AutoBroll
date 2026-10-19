"""Tests for job state persistence and crash recovery on application restart (Requirement 9.10)."""
from app.core.database import db

def test_interrupted_jobs_recovered_on_restart():
    # Simulate a job that was in 'processing' state when the server crashed
    job_id = "job-interrupted-by-crash-001"
    req_payload = {"script": "Long running job interrupted by server crash."}
    db.create_job(job_id, req_payload)
    db.start_job(job_id)

    # Verify status is processing
    pre_restart = db.get_job(job_id)
    assert pre_restart["status"] == "processing"

    # Simulate server restart lifecycle invoking recovery
    recovered_ids = db.recover_interrupted_jobs()
    assert job_id in recovered_ids

    # Verify job is now marked failed with INTERRUPTED_BY_RESTART and retryable=True
    post_restart = db.get_job(job_id)
    assert post_restart["status"] == "failed"
    assert post_restart["stage"] == "interrupted"
    assert post_restart["error_payload"]["code"] == "INTERRUPTED_BY_RESTART"
    assert post_restart["error_payload"]["retryable"] is True
