"""Tests for Security: Path-traversal protection and Secret Redaction (Requirement 9.13 & 9.14)."""
import json
import logging
from app.core.logging import JsonFormatter, redact_secrets

def test_download_path_traversal_prevention(client):
    # Attempt directory traversal via job_id
    res1 = client.get("/api/v1/jobs/download/..%2F..%2Fetc/passwd")
    assert res1.status_code in [400, 404]

    # Attempt directory traversal via filename
    res2 = client.get("/api/v1/jobs/download/valid-uuid/..%2F..%2Fwindows%2Fwin.ini")
    assert res2.status_code in [400, 404]

def test_secret_redaction_in_log_formatter():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Initiating API call with api_key='secret_pexels_999888777' and Bearer sk-ant-api03-abcdef1234567890",
        args=(),
        exc_info=None,
    )
    formatted_json = formatter.format(record)
    log_data = json.loads(formatted_json)

    # Verify secrets are redacted
    assert "secret_pexels_999888777" not in log_data["message"]
    assert "sk-ant-api03-abcdef1234567890" not in log_data["message"]
    assert "[REDACTED]" in log_data["message"]

def test_redact_secrets_function():
    raw_text = "PEXELS_API_KEY=ABC123456789 and token='xyz987654321'"
    clean_text = redact_secrets(raw_text)
    assert "ABC123456789" not in clean_text
    assert "xyz987654321" not in clean_text
    assert "[REDACTED]" in clean_text
