#!/usr/bin/env python3
"""IncuBrix Batch Runner CLI Tool (Integration Standard IS-10).

Processes a directory or single file of test inputs (scripts or job definitions),
executes jobs via direct in-process engine or HTTP API, organizes all generated
assets (videos, timelines, captions, manifests) into a designated output folder,
and produces a structured execution summary.

Usage:
    python cli/batch_runner.py --input test_pack/inputs --output-dir test_pack/outputs
    python cli/batch_runner.py --input test_pack/inputs/01_finance.json --output-dir test_pack/outputs
    python cli/batch_runner.py --input test_pack/inputs --api-url http://127.0.0.1:8000
"""
import argparse
import asyncio
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure repository root and backend directory are in sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BACKEND_DIR = REPO_ROOT / "backend"

for d in [str(REPO_ROOT), str(BACKEND_DIR)]:
    if d not in sys.path:
        sys.path.insert(0, d)


class BatchJobItem:
    """Represents a single job item to be processed in the batch."""

    def __init__(
        self,
        name: str,
        payload: Dict[str, Any],
        source_file: Optional[Path] = None,
    ):
        self.name = name
        self.payload = payload
        self.source_file = source_file
        self.job_id: Optional[str] = None
        self.status: str = "pending"
        self.error: Optional[str] = None
        self.duration_seconds: float = 0.0
        self.video_duration: float = 0.0
        self.scene_count: int = 0
        self.output_files: Dict[str, Path] = {}
        self.manifest: Optional[Dict[str, Any]] = None
        self.timeline: Optional[Dict[str, Any]] = None


def load_input_items(
    input_path: Path,
    default_voice: str = "en-IN-female",
    default_ratios: Optional[List[str]] = None,
    burn_in_captions: bool = True,
) -> List[BatchJobItem]:
    """Scan and parse input files into BatchJobItem instances."""
    if default_ratios is None:
        default_ratios = ["16:9", "9:16"]

    input_path = Path(input_path).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")

    files_to_process: List[Path] = []
    if input_path.is_file():
        files_to_process.append(input_path)
    elif input_path.is_dir():
        for ext in ("*.json", "*.txt"):
            files_to_process.extend(sorted(input_path.glob(ext)))

    if not files_to_process:
        raise ValueError(f"No .json or .txt input files found at: {input_path}")

    items: List[BatchJobItem] = []

    for file_path in files_to_process:
        stem = file_path.stem
        if file_path.suffix.lower() == ".txt":
            content = file_path.read_text(encoding="utf-8").strip()
            if not content:
                continue
            payload = {
                "script": content,
                "voice": default_voice,
                "aspect_ratios": default_ratios,
                "captions": {"burn_in": burn_in_captions, "srt": True},
            }
            items.append(BatchJobItem(name=stem, payload=payload, source_file=file_path))

        elif file_path.suffix.lower() == ".json":
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[WARN] Failed to parse JSON file {file_path.name}: {e}")
                continue

            if isinstance(data, list):
                for idx, entry in enumerate(data):
                    item_name = entry.get("name") or f"{stem}_{idx+1:02d}"
                    payload = _normalize_payload(entry, default_voice, default_ratios, burn_in_captions)
                    items.append(BatchJobItem(name=item_name, payload=payload, source_file=file_path))
            elif isinstance(data, dict):
                item_name = data.get("name") or stem
                payload = _normalize_payload(data, default_voice, default_ratios, burn_in_captions)
                items.append(BatchJobItem(name=item_name, payload=payload, source_file=file_path))

    return items


def _normalize_payload(
    data: Dict[str, Any],
    default_voice: str,
    default_ratios: List[str],
    default_burn_in: bool,
) -> Dict[str, Any]:
    """Ensure all required payload fields exist with appropriate defaults."""
    payload = dict(data)
    if "voice" not in payload:
        payload["voice"] = default_voice
    if "aspect_ratios" not in payload:
        payload["aspect_ratios"] = default_ratios
    if "captions" not in payload:
        payload["captions"] = {"burn_in": default_burn_in, "srt": True}
    return payload


async def execute_job_direct(
    item: BatchJobItem,
    output_dir: Path,
) -> None:
    """Execute job directly via in-process job_runner."""
    from app.core.config import settings
    from app.core.database import db
    from app.workers.job_runner import job_runner

    timestamp = int(time.time() * 1000)
    job_id = f"batch-{timestamp}-{item.name[:24]}"
    item.job_id = job_id

    # Create job in database
    db.create_job(job_id, item.payload)

    t0 = time.time()
    await job_runner.execute_job(job_id)
    item.duration_seconds = round(time.time() - t0, 2)

    record = db.get_job(job_id)
    if not record or record.get("status") != "completed":
        item.status = "failed"
        item.error = (record.get("error") if record else "Job did not complete successfully") or "Unknown error"
        return

    item.status = "completed"

    # Copy output files from storage to designated output folder
    job_storage = Path(settings.LOCAL_STORAGE_DIR) / job_id
    dest_dir = output_dir / item.name
    dest_dir.mkdir(parents=True, exist_ok=True)

    if job_storage.exists():
        for f in job_storage.iterdir():
            if f.is_file():
                dest_file = dest_dir / f.name
                shutil.copyfile(f, dest_file)
                item.output_files[f.name] = dest_file

    # Load timeline & manifest metadata
    timeline_file = dest_dir / "timeline.json"
    if timeline_file.exists():
        try:
            item.timeline = json.loads(timeline_file.read_text(encoding="utf-8"))
            item.video_duration = item.timeline.get("total_duration", 0.0)
            item.scene_count = item.timeline.get("total_scenes", 0)
        except Exception:
            pass

    manifest_file = dest_dir / "manifest.json"
    if manifest_file.exists():
        try:
            item.manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except Exception:
            pass


def execute_job_http(
    item: BatchJobItem,
    api_url: str,
    output_dir: Path,
    timeout_seconds: int = 180,
) -> None:
    """Execute job against running HTTP API server."""
    base_url = api_url.rstrip("/")
    submit_url = f"{base_url}/api/v1/broll/jobs"

    idemp_key = f"batch-cli-{int(time.time())}-{item.name[:20]}"
    body = json.dumps(item.payload).encode("utf-8")
    req = urllib.request.Request(submit_url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Idempotency-Key", idemp_key)

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            job_id = data.get("job_id")
            item.job_id = job_id
    except Exception as e:
        item.status = "failed"
        item.error = f"API submission failed: {e}"
        item.duration_seconds = round(time.time() - t0, 2)
        return

    # Poll status
    status_url = f"{base_url}/api/v1/broll/jobs/{job_id}"
    start_poll = time.time()
    final_data: Optional[Dict[str, Any]] = None

    while time.time() - start_poll < timeout_seconds:
        time.sleep(0.5)
        try:
            req_poll = urllib.request.Request(status_url, method="GET")
            with urllib.request.urlopen(req_poll, timeout=10) as resp:
                poll_data = json.loads(resp.read().decode("utf-8"))
                st = poll_data.get("status")
                if st in ("completed", "failed"):
                    final_data = poll_data
                    break
        except Exception:
            pass

    item.duration_seconds = round(time.time() - t0, 2)

    if not final_data or final_data.get("status") != "completed":
        item.status = "failed"
        item.error = (final_data.get("error") if final_data else "Job timed out or failed") or "Unknown error"
        return

    item.status = "completed"
    dest_dir = output_dir / item.name
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Download result files
    result_urls = final_data.get("result_urls", {})
    for out_name, dl_url in result_urls.items():
        try:
            filename = dl_url.split("/")[-1]
            dest_file = dest_dir / filename
            urllib.request.urlretrieve(dl_url, str(dest_file))
            item.output_files[filename] = dest_file
        except Exception as e:
            print(f"  [WARN] Failed to download {out_name}: {e}")

    # Load timeline and manifest if downloaded
    timeline_file = dest_dir / "timeline.json"
    if timeline_file.exists():
        try:
            item.timeline = json.loads(timeline_file.read_text(encoding="utf-8"))
            item.video_duration = item.timeline.get("total_duration", 0.0)
            item.scene_count = item.timeline.get("total_scenes", 0)
        except Exception:
            pass

    manifest_file = dest_dir / "manifest.json"
    if manifest_file.exists():
        try:
            item.manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except Exception:
            pass


def generate_batch_reports(
    items: List[BatchJobItem],
    output_dir: Path,
    total_elapsed: float,
) -> Tuple[Path, Path]:
    """Generate batch_summary.json and batch_summary.md in the output directory."""
    succeeded = sum(1 for it in items if it.status == "completed")
    failed = len(items) - succeeded
    avg_duration = round(sum(it.duration_seconds for it in items) / len(items), 2) if items else 0.0

    summary_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_jobs": len(items),
        "succeeded": succeeded,
        "failed": failed,
        "success_rate_percent": round((succeeded / len(items)) * 100, 1) if items else 0.0,
        "total_elapsed_seconds": round(total_elapsed, 2),
        "average_job_duration_seconds": avg_duration,
        "jobs": [
            {
                "name": it.name,
                "job_id": it.job_id,
                "status": it.status,
                "error": it.error,
                "duration_seconds": it.duration_seconds,
                "video_duration": it.video_duration,
                "scene_count": it.scene_count,
                "output_files": [p.name for p in it.output_files.values()],
            }
            for it in items
        ],
    }

    # 1. Write batch_summary.json
    json_path = output_dir / "batch_summary.json"
    json_path.write_text(json.dumps(summary_data, indent=2), encoding="utf-8")

    # 2. Write batch_summary.md
    md_lines = [
        "# Batch Execution Summary Report",
        f"**Date:** {summary_data['timestamp']}  ",
        f"**Total Jobs:** {len(items)} | **Succeeded:** {succeeded} | **Failed:** {failed} | **Success Rate:** {summary_data['success_rate_percent']}%  ",
        f"**Total Elapsed Time:** {summary_data['total_elapsed_seconds']}s | **Average per Job:** {avg_duration}s  ",
        "",
        "## Job Results",
        "| # | Job Name | Status | Execution Time | Scenes | Video Duration | Output Files |",
        "|---|---|---|---|---|---|---|",
    ]

    for idx, it in enumerate(items, 1):
        status_badge = "✅ Completed" if it.status == "completed" else "❌ Failed"
        files_str = ", ".join(f"`{k}`" for k in sorted(it.output_files.keys())) if it.output_files else (it.error or "None")
        md_lines.append(
            f"| {idx} | **{it.name}** | {status_badge} | {it.duration_seconds}s | {it.scene_count} | {it.video_duration}s | {files_str} |"
        )

    md_lines.append("")
    md_lines.append("---")
    md_lines.append("*Generated automatically by IncuBrix Batch Runner CLI (Integration Standard IS-10)*")

    md_path = output_dir / "batch_summary.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return json_path, md_path


def print_cli_summary(
    items: List[BatchJobItem],
    total_elapsed: float,
    json_report: Path,
    md_report: Path,
) -> None:
    """Print clean formatted summary to stdout."""
    succeeded = sum(1 for it in items if it.status == "completed")
    failed = len(items) - succeeded

    print("\n" + "=" * 78)
    print("                      INCUBRIX BATCH RUNNER SUMMARY")
    print("=" * 78)
    print(f"Total Jobs Processed : {len(items)}")
    print(f"Succeeded            : {succeeded} ({(succeeded / len(items) * 100):.1f}%)" if items else "0")
    print(f"Failed               : {failed}")
    print(f"Total Execution Time : {total_elapsed:.2f}s")
    print("-" * 78)
    print(f"{'#':<3} {'Job Name':<25} {'Status':<12} {'Time (s)':<10} {'Scenes':<8} {'Outputs'}")
    print("-" * 78)

    for idx, it in enumerate(items, 1):
        status_disp = "SUCCESS" if it.status == "completed" else "FAILED"
        outputs_disp = f"{len(it.output_files)} assets" if it.output_files else (it.error or "-")
        if len(outputs_disp) > 20:
            outputs_disp = outputs_disp[:17] + "..."
        print(f"{idx:<3} {it.name[:24]:<25} {status_disp:<12} {it.duration_seconds:<10.2f} {it.scene_count:<8} {outputs_disp}")

    print("-" * 78)
    print(f"Detailed JSON Report : {json_report}")
    print(f"Markdown Summary     : {md_report}")
    print("=" * 78 + "\n")


def run_batch(
    input_path: str,
    output_dir: str = "test_pack/outputs",
    api_url: Optional[str] = None,
    default_voice: str = "en-IN-female",
    ratios: str = "16:9,9:16",
    burn_in_captions: bool = True,
    fail_fast: bool = False,
) -> int:
    """Execute batch runner logic."""
    target_ratios = [r.strip() for r in ratios.split(",") if r.strip()]
    out_dir_path = Path(output_dir).resolve()
    out_dir_path.mkdir(parents=True, exist_ok=True)

    print(f"\n[BatchRunner] Scanning input: {input_path}")
    items = load_input_items(
        input_path=Path(input_path),
        default_voice=default_voice,
        default_ratios=target_ratios,
        burn_in_captions=burn_in_captions,
    )
    print(f"[BatchRunner] Found {len(items)} job(s) to process. Output directory: {out_dir_path}")

    mode_label = f"HTTP API ({api_url})" if api_url else "Direct In-Process Engine"
    print(f"[BatchRunner] Execution Mode: {mode_label}\n")

    t_start = time.time()

    for idx, item in enumerate(items, 1):
        print(f"--> [{idx}/{len(items)}] Processing '{item.name}'...")
        try:
            if api_url:
                execute_job_http(item, api_url=api_url, output_dir=out_dir_path)
            else:
                asyncio.run(execute_job_direct(item, output_dir=out_dir_path))
        except Exception as e:
            item.status = "failed"
            item.error = str(e)

        if item.status == "completed":
            print(f"    [OK] Finished in {item.duration_seconds:.2f}s ({item.scene_count} scenes, {len(item.output_files)} files generated)")
        else:
            print(f"    [ERR] Job failed: {item.error}")
            if fail_fast:
                print("[BatchRunner] Halting on fail-fast request.")
                break

    total_elapsed = time.time() - t_start
    json_rep, md_rep = generate_batch_reports(items, out_dir_path, total_elapsed)
    print_cli_summary(items, total_elapsed, json_rep, md_rep)

    return 0 if all(it.status == "completed" for it in items) else 1


def main():
    parser = argparse.ArgumentParser(
        description="IncuBrix Batch Runner CLI Tool (Integration Standard IS-10)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Path to an input directory (.json / .txt files) or a single input file",
    )
    parser.add_argument(
        "--output-dir", "-o",
        default="test_pack/outputs",
        help="Directory to save all generated job outputs and summary reports",
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help="Optional URL of running IncuBrix API server. If omitted, runs in direct engine mode",
    )
    parser.add_argument(
        "--voice",
        default="en-IN-female",
        help="Default narration voice identifier",
    )
    parser.add_argument(
        "--ratios",
        default="16:9,9:16",
        help="Comma-separated target aspect ratios",
    )
    parser.add_argument(
        "--no-burn-in",
        action="store_true",
        help="Disable burned-in captions on the video output",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop the batch immediately upon first failure",
    )

    args = parser.parse_args()

    exit_code = run_batch(
        input_path=args.input,
        output_dir=args.output_dir,
        api_url=args.api_url,
        default_voice=args.voice,
        ratios=args.ratios,
        burn_in_captions=not args.no_burn_in,
        fail_fast=args.fail_fast,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
