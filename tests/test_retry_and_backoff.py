"""Tests for Retry limits, transient detection, and exponential backoff (Requirement 9.7 & 9.8)."""
import asyncio
import pytest
import httpx
from app.core.exceptions import InvalidInputError, ProviderUnavailableError
from app.core.retry import execute_with_retry, is_transient_error

@pytest.mark.asyncio
async def test_retry_transient_error_until_success():
    attempts = 0

    async def flaky_operation():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            # Raise transient 503 HTTPStatusError
            request = httpx.Request("GET", "https://api.pexels.test")
            response = httpx.Response(status_code=503, request=request)
            raise httpx.HTTPStatusError("Service Unavailable", request=request, response=response)
        return "SUCCESS"

    result = await execute_with_retry(
        flaky_operation,
        max_retries=3,
        base_delay=0.01,
        max_delay=0.05,
        operation_name="flaky_test",
    )
    assert result == "SUCCESS"
    assert attempts == 3

@pytest.mark.asyncio
async def test_retry_exceeds_max_attempts():
    attempts = 0

    async def failing_operation():
        nonlocal attempts
        attempts += 1
        request = httpx.Request("GET", "https://api.pexels.test")
        response = httpx.Response(status_code=504, request=request)
        raise httpx.HTTPStatusError("Gateway Timeout", request=request, response=response)

    with pytest.raises(httpx.HTTPStatusError):
        await execute_with_retry(
            failing_operation,
            max_retries=2,
            base_delay=0.01,
            max_delay=0.05,
            operation_name="timeout_test",
        )
    assert attempts == 3  # 1 initial + 2 retries

@pytest.mark.asyncio
async def test_non_transient_error_not_retried():
    attempts = 0

    async def invalid_op():
        nonlocal attempts
        attempts += 1
        raise InvalidInputError("Permanent validation failure")

    with pytest.raises(InvalidInputError):
        await execute_with_retry(
            invalid_op,
            max_retries=5,
            base_delay=0.01,
            operation_name="invalid_op",
        )
    assert attempts == 1  # Should fail immediately without retry

def test_transient_error_detection():
    # Transient
    assert is_transient_error(httpx.TimeoutException("Timeout"))
    assert is_transient_error(ConnectionError("Conn reset"))
    assert is_transient_error(ProviderUnavailableError("pexels"))

    # Permanent
    assert not is_transient_error(InvalidInputError("bad param"))
    assert not is_transient_error(ValueError("syntax error"))
