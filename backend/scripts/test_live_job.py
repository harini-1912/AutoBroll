"""Connect to live running HTTP server and test end-to-end video job processing."""
import json
import time
import urllib.request
import urllib.error

BASE_URL = "http://127.0.0.1:8000"

def post_json(url, data, headers=None):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

def get_json(url):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

def get_raw(url):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req) as resp:
        return resp.status, resp.read()

def main():
    print("=== LIVE SERVER VERIFICATION ===")
    
    # 1. Health check
    h_code, h_data = get_json(f"{BASE_URL}/health")
    print(f"Health Probe ({h_code}): {h_data}")
    assert h_code == 200, "Health probe failed"

    # 2. Submit real video generation job
    payload = {
        "script": "Saving money today provides resilience tomorrow. Smart investments and steady growth build long-term freedom.",
        "voice": "en-IN-female",
        "aspect_ratios": ["16:9", "9:16"],
        "style": "bright, modern, real-world footage",
        "avoid": ["alcohol", "faces"],
        "captions": {"burn_in": True, "srt": True}
    }
    idemp_key = f"live-test-{int(time.time())}"
    print(f"\nSubmitting job to {BASE_URL}/api/v1/broll/jobs (Idempotency-Key: {idemp_key})...")
    status_code, job_resp = post_json(
        f"{BASE_URL}/api/v1/broll/jobs",
        payload,
        headers={"Idempotency-Key": idemp_key}
    )
    print(f"Job Creation Status: {status_code}")
    print(f"Initial Response: {json.dumps(job_resp, indent=2)}")
    assert status_code == 202, f"Expected 202, got {status_code}"
    job_id = job_resp["job_id"]

    # 3. Poll job status
    print(f"\nPolling status for job {job_id}...")
    terminal_status = None
    final_data = None
    for attempt in range(30):
        time.sleep(0.15)
        p_code, p_data = get_json(f"{BASE_URL}/api/v1/broll/jobs/{job_id}")
        print(f"[{attempt+1:02d}] Status: {p_data['status']:12s} | Stage: {p_data['stage']:20s} | Progress: {p_data['progress']}%")
        if p_data["status"] in ["completed", "failed"]:
            terminal_status = p_data["status"]
            final_data = p_data
            break

    print(f"\nTerminal Status Reached: {terminal_status}")
    assert terminal_status == "completed", f"Job failed or did not complete: {final_data}"
    assert final_data["progress"] == 100, f"Expected 100% progress, got {final_data['progress']}"
    assert final_data["result_urls"] is not None, "result_urls missing"

    # 4. Check Timeline endpoint
    t_code, t_data = get_json(f"{BASE_URL}/api/v1/broll/jobs/{job_id}/timeline")
    print(f"\nTimeline Endpoint ({t_code}):")
    print(f" - Total scenes: {t_data.get('total_scenes')}")
    print(f" - Duration: {t_data.get('total_duration')}s")
    for s in t_data.get("scenes", []):
        print(f"   * Scene {s['scene_id']}: '{s['text'][:30]}...' -> Clip: {s['clip']['clip_id']} ({s['clip']['provider']})")

    # 5. Check Manifest endpoint
    m_code, m_data = get_json(f"{BASE_URL}/api/v1/broll/jobs/{job_id}/manifest")
    print(f"\nManifest Endpoint ({m_code}):")
    print(f" - Processing time: {m_data.get('total_processing_time_seconds')}s")
    print(f" - Total cost: ${m_data.get('cost_estimate', {}).get('total_usd')}")
    print(f" - Outputs recorded: {list(m_data.get('outputs', {}).keys())}")

    # 6. Verify downloads for all result_urls
    print("\nVerifying Output Downloads:")
    for name, dl_url in final_data["result_urls"].items():
        dl_code, dl_bytes = get_raw(dl_url)
        print(f" - {name:15s} [{dl_code}]: {len(dl_bytes)} bytes downloaded from {dl_url}")
        assert dl_code == 200, f"Failed to download {name}"
        assert len(dl_bytes) > 0, f"Downloaded 0 bytes for {name}"

    print("\n>>> ALL LIVE CHECKS PASSED SUCCESSFULLY! <<<")

if __name__ == "__main__":
    main()
