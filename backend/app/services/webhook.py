"""Webhook callback service conforming to Integration Standard #3."""
import hashlib
import hmac
import json
from typing import Any, Dict
import httpx
from app.core.config import settings
from app.core.logging import logger

async def send_job_webhook(callback_url: str, job_id: str, payload: Dict[str, Any]) -> bool:
    """Send job completion webhook signed with HMAC-SHA256 in X-IncuBrix-Signature header."""
    try:
        body = json.dumps(payload, sort_keys=True)
        secret = settings.WEBHOOK_SIGNATURE_SECRET.encode("utf-8")
        signature = hmac.new(secret, body.encode("utf-8"), hashlib.sha256).hexdigest()

        headers = {
            "Content-Type": "application/json",
            "X-IncuBrix-Signature": signature,
            "X-IncuBrix-Job-Id": job_id,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(callback_url, content=body, headers=headers)
            logger.info(
                "Webhook delivered for job %s to %s with status %s",
                job_id,
                callback_url,
                response.status_code,
            )
            return response.is_success
    except Exception as e:
        logger.warning("Failed to deliver webhook for job %s to %s: %s", job_id, callback_url, str(e))
        return False
