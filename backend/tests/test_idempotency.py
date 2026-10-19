"""Tests for Idempotency-Key support conforming to Integration Standard #2."""

def test_idempotent_post_returns_same_job(client):
    idempotency_key = "idemp-key-abc-123"
    payload = {
        "script": "Idempotency test line for B-roll engine.",
        "aspect_ratios": ["16:9"],
    }

    # First request
    res1 = client.post("/api/v1/broll/jobs", json=payload, headers={"Idempotency-Key": idempotency_key})
    assert res1.status_code == 202
    data1 = res1.json()
    job_id1 = data1["job_id"]

    # Second request with identical key and payload
    res2 = client.post("/api/v1/broll/jobs", json=payload, headers={"Idempotency-Key": idempotency_key})
    assert res2.status_code == 202
    data2 = res2.json()
    job_id2 = data2["job_id"]

    assert job_id1 == job_id2, "Idempotent request must return the exact same job ID"

def test_idempotent_post_with_different_payload_returns_409(client):
    idempotency_key = "idemp-key-conflict-456"
    payload1 = {"script": "First payload.", "aspect_ratios": ["16:9"]}
    payload2 = {"script": "Second different payload.", "aspect_ratios": ["9:16"]}

    res1 = client.post("/api/v1/broll/jobs", json=payload1, headers={"Idempotency-Key": idempotency_key})
    assert res1.status_code == 202

    res2 = client.post("/api/v1/broll/jobs", json=payload2, headers={"Idempotency-Key": idempotency_key})
    assert res2.status_code == 409
    data2 = res2.json()
    assert data2["error"]["code"] == "IDEMPOTENCY_CONFLICT"
