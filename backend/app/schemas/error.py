"""Standard Error Schema conforming to Integration Standard #5."""
from typing import Any, Dict
from pydantic import BaseModel, Field

class ErrorDetail(BaseModel):
    code: str = Field(..., description="UPPER_SNAKE_CASE error code", example="INVALID_INPUT")
    message: str = Field(..., description="Human-readable error description")
    retryable: bool = Field(False, description="Whether client should safely retry request")
    details: Dict[str, Any] = Field(default_factory=dict, description="Diagnostic details")

class ErrorResponse(BaseModel):
    error: ErrorDetail
