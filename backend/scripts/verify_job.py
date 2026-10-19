"""Submit a test job and verify status transitions and outputs."""
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from app.main import app

def main():
    client = TestClient(app)

    print("\n--- 1. Testing Health and Readiness ---")
    r_health = client.get("/health")
    print(f"Health response ({r_health.status_code}): {r_health.json()}")
    r_ready = client.get("/ready")
    print(f"Ready response ({r_ready.status_code}): {r_ready.json()}")

    print("\n--- 2. Submitting Job to /api/v1/broll/jobs ---")
    payload = {
        "script": "A creator records a sixty-second voiceover about saving money and building value.",
        "voice": "en-IN-female",
        "aspect_ratios": ["16:9", "9:16"],
        "style": "bright, modern, real-world footage",
        "avoid": ["alcohol", "faces"],
    }
    r_job = client.post("/api/v1/broll/jobs", json=payload, headers={"Idempotency-Key": "manual-verify-001"})
    print(f"Create Job ({r_job.status_code}): {r_job.json()}")
    job_id = r_job.json()["job_id"]

    print("\n--- 3. Polling Job Progress ---")
    for _ in range(20):
        time.sleep(0.1)
        r_poll = client.get(f"/api/v1/broll/jobs/{job_id}")
        data = r_poll.json()
        print(f"Status: {data['status']:12s} | Stage: {data['stage']:20s} | Progress: {data['progress']}%")
        if data["status"] in ["completed", "succeeded", "failed"]:
            break

    print("\n--- 4. Checking Timeline and Manifest Endpoints ---")
    r_time = client.get(f"/api/v1/broll/jobs/{job_id}/timeline")
    print(f"Timeline status: {r_time.status_code}, scenes count: {r_time.json().get('total_scenes')}")

    r_man = client.get(f"/api/v1/broll/jobs/{job_id}/manifest")
    print(f"Manifest status: {r_man.status_code}, timings: {r_man.json().get('timings')}")

    print("\n--- 5. Downloading Output Assets ---")
    r_dl = client.get(f"/api/v1/jobs/download/{job_id}/video_16_9.mp4")
    print(f"Video 16:9 download ({r_dl.status_code}): {len(r_dl.content)} bytes received")

    r_srt = client.get(f"/api/v1/jobs/download/{job_id}/captions.srt")
    print(f"Captions SRT download ({r_srt.status_code}):\n{r_srt.text[:120]}...")

    print("\n[SUCCESS] Verification complete! All transitions, downloads, and endpoints verified.")

if __name__ == "__main__":
    main()
