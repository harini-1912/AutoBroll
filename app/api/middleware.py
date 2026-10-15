"""API Middleware for Correlation-ID, Idempotency, and Structured Logging."""
import hashlib
import json
import uuid
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.database import db
from app.core.exceptions import IdempotencyConflictError
from app.core.logging import correlation_id_ctx, logger

class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Ensures every request has an X-Correlation-Id header and context."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        corr_id = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
        correlation_id_ctx.set(corr_id)
        request.state.correlation_id = corr_id

        response = await call_next(request)
        response.headers["X-Correlation-Id"] = corr_id
        return response

def hash_payload(body_bytes: bytes) -> str:
    """Compute SHA-256 hash of request body for idempotency comparison."""
    return hashlib.sha256(body_bytes).hexdigest()
