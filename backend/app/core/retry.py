"""Retry and Exponential Backoff Engine conforming to Integration Standard #4."""
import asyncio
import random
from typing import Any, Callable, Coroutine, Optional, Set, Type
import httpx
from app.core.exceptions import AppException
from app.core.logging import logger

TRANSIENT_STATUS_CODES: Set[int] = {408, 429, 500, 502, 503, 504}

def is_transient_error(exc: Exception) -> bool:
    """Determine whether an error is transient and safe to retry."""
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError, ConnectionError, TimeoutError)):
        return True

    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in TRANSIENT_STATUS_CODES

    if isinstance(exc, AppException):
        return exc.retryable

    return False

def get_retry_after(exc: Exception) -> Optional[float]:
    """Extract Retry-After header duration if present in HTTP 429/503 responses."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        header_val = exc.response.headers.get("Retry-After")
        if header_val:
            try:
                return float(header_val)
            except ValueError:
                pass
    return None

async def execute_with_retry(
    func: Callable[..., Coroutine[Any, Any, Any]],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    backoff_factor: float = 2.0,
    operation_name: str = "operation",
    **kwargs: Any,
) -> Any:
    """Execute an async operation with bounded retries and exponential backoff."""
    attempt = 0
    while True:
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            attempt += 1
            if attempt > max_retries or not is_transient_error(exc):
                logger.warning(
                    "%s failed permanently after %d attempt(s): %s",
                    operation_name,
                    attempt,
                    str(exc),
                )
                raise exc

            # Calculate delay
            retry_after = get_retry_after(exc)
            if retry_after is not None and retry_after > 0:
                delay = min(retry_after, max_delay)
            else:
                jitter = random.uniform(0.8, 1.2)
                delay = min(base_delay * (backoff_factor ** (attempt - 1)) * jitter, max_delay)

            logger.info(
                "%s failed with transient error: %s. Retrying in %.2fs (attempt %d/%d)...",
                operation_name,
                str(exc),
                delay,
                attempt,
                max_retries,
            )
            await asyncio.sleep(delay)
