"""Structured JSON logging module conforming to Integration Standard #8."""
import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Optional

correlation_id_ctx: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)
job_id_ctx: ContextVar[Optional[str]] = ContextVar("job_id", default=None)

import re

SENSITIVE_PATTERNS = [
    re.compile(r"(api[_-]?key|secret|token|password|authorization)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-\.]{6,})['\"]?", re.IGNORECASE),
    re.compile(r"(Bearer\s+)([a-zA-Z0-9_\-\.]{8,})", re.IGNORECASE),
]

def redact_secrets(text: str) -> str:
    """Mask credentials and sensitive strings in log outputs."""
    redacted = text
    for pattern in SENSITIVE_PATTERNS:
        redacted = pattern.sub(r"\1 [REDACTED]", redacted)
    return redacted

class JsonFormatter(logging.Formatter):
    """Formats log records as structured JSON with secret redaction."""

    def format(self, record: logging.LogRecord) -> str:
        msg = redact_secrets(record.getMessage())
        log_payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": msg,
            "logger": record.name,
            "correlation_id": correlation_id_ctx.get() or getattr(record, "correlation_id", None),
            "job_id": job_id_ctx.get() or getattr(record, "job_id", None),
        }

        if record.exc_info:
            log_payload["exception"] = redact_secrets(self.formatException(record.exc_info))

        # Include custom extra attributes if present
        for key in ("stage", "error_code", "duration_ms", "details"):
            if hasattr(record, key):
                val = getattr(record, key)
                if isinstance(val, str):
                    val = redact_secrets(val)
                log_payload[key] = val

        return json.dumps(log_payload)

def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """Configure root logger with structured JSON output."""
    root_logger = logging.getLogger()
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger.setLevel(numeric_level)

    # Avoid duplicate handlers
    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root_logger.addHandler(handler)
    else:
        for handler in root_logger.handlers:
            handler.setFormatter(JsonFormatter())

    # Suppress verbose third party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    return root_logger

logger = logging.getLogger("incubrix")
