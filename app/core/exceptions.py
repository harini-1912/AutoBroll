"""Application exceptions conforming to Integration Standard #5."""
from typing import Any, Dict, Optional

class AppException(Exception):
    """Base application exception returning structured error payload."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        retryable: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "details": self.details,
            }
        }

class InvalidInputError(AppException):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code="INVALID_INPUT",
            message=message,
            status_code=400,
            retryable=False,
            details=details,
        )

class JobNotFoundError(AppException):
    def __init__(self, job_id: str):
        super().__init__(
            code="JOB_NOT_FOUND",
            message=f"Job with ID '{job_id}' was not found.",
            status_code=404,
            retryable=False,
            details={"job_id": job_id},
        )

class SceneNotFoundError(AppException):
    def __init__(self, scene_id: str, job_id: str):
        super().__init__(
            code="SCENE_NOT_FOUND",
            message=f"Scene '{scene_id}' in job '{job_id}' was not found.",
            status_code=404,
            retryable=False,
            details={"scene_id": scene_id, "job_id": job_id},
        )

class UnsupportedMediaTypeError(AppException):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code="UNSUPPORTED_MEDIA_TYPE",
            message=message,
            status_code=415,
            retryable=False,
            details=details,
        )

class PayloadTooLargeError(AppException):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code="PAYLOAD_TOO_LARGE",
            message=message,
            status_code=413,
            retryable=False,
            details=details,
        )

class AudioProcessingError(AppException):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code="AUDIO_PROCESSING_ERROR",
            message=message,
            status_code=422,
            retryable=False,
            details=details,
        )

class TTSGenerationError(AppException):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code="TTS_GENERATION_FAILED",
            message=message,
            status_code=502,
            retryable=True,
            details=details,
        )

class FootageNotFoundError(AppException):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code="FOOTAGE_NOT_FOUND",
            message=message,
            status_code=502,
            retryable=True,
            details=details,
        )

class RenderFailedError(AppException):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code="RENDER_FAILED",
            message=message,
            status_code=500,
            retryable=True,
            details=details,
        )

class RateLimitExceededError(AppException):
    def __init__(self, message: str = "Rate limit exceeded. Please wait before retrying."):
        super().__init__(
            code="RATE_LIMITED",
            message=message,
            status_code=429,
            retryable=True,
            details={},
        )

class StorageError(AppException):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code="STORAGE_ERROR",
            message=message,
            status_code=503,
            retryable=True,
            details=details,
        )

class ProviderUnavailableError(AppException):
    def __init__(self, provider: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code="PROVIDER_UNAVAILABLE",
            message=f"Provider '{provider}' is currently unavailable.",
            status_code=503,
            retryable=True,
            details=details or {"provider": provider},
        )

class IdempotencyConflictError(AppException):
    def __init__(self, message: str = "A request with this Idempotency-Key is already being processed with different parameters."):
        super().__init__(
            code="IDEMPOTENCY_CONFLICT",
            message=message,
            status_code=409,
            retryable=False,
            details={},
        )
