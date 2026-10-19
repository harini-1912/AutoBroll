"""Test Batch Runner CLI conforming to Integration Standard IS-10."""
import json
import subprocess
import sys
from pathlib import Path

import pytest


def test_batch_runner_cli_help():
    """Verify that batch_runner CLI help executes cleanly with 0 exit code."""
    res = subprocess.run(
        [sys.executable, "cli/batch_runner.py", "--help"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "IncuBrix Batch Runner CLI Tool" in res.stdout
    assert "--input" in res.stdout
    assert "--output-dir" in res.stdout


def test_batch_runner_execution_on_test_inputs(tmp_path):
    """Verify executing batch_runner on a sample input produces valid output assets and summary."""
    input_file = tmp_path / "sample_script.txt"
    input_file.write_text("Automated testing ensures stability. Fast feedback cycles accelerate delivery.", encoding="utf-8")

    out_dir = tmp_path / "batch_out"

    res = subprocess.run(
        [
            sys.executable,
            "cli/batch_runner.py",
            "--input",
            str(input_file),
            "--output-dir",
            str(out_dir),
            "--ratios",
            "16:9",
        ],
        capture_output=True,
        text=True,
    )

    assert res.returncode == 0, f"Batch runner failed: {res.stderr}"
    assert "INCUBRIX BATCH RUNNER SUMMARY" in res.stdout

    # Verify reports generated
    json_summary = out_dir / "batch_summary.json"
    md_summary = out_dir / "batch_summary.md"
    assert json_summary.exists()
    assert md_summary.exists()

    data = json.loads(json_summary.read_text(encoding="utf-8"))
    assert data["total_jobs"] == 1
    assert data["succeeded"] == 1
    assert data["failed"] == 0

    # Verify job output folder
    job_dir = out_dir / "sample_script"
    assert job_dir.exists()
    assert (job_dir / "video_16_9.mp4").exists()
    assert (job_dir / "captions.srt").exists()
    assert (job_dir / "timeline.json").exists()
    assert (job_dir / "manifest.json").exists()
