"""Job Manifest Schema conforming to Integration Standard #9."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class ModelProviderInfo(BaseModel):
    name: str
    version: str
    purpose: str

class VideoOutputInfo(BaseModel):
    aspect_ratio: str
    resolution: str
    fps: int
    codec: str
    audio_codec: str
    file_size_bytes: int
    duration_seconds: float
    url: str

class JobManifest(BaseModel):
    job_id: str
    created_at: str
    completed_at: str
    total_processing_time_seconds: float
    seed: Optional[int] = 42
    settings: Dict[str, Any]
    models_and_providers: List[ModelProviderInfo]
    timings: Dict[str, float]  # stage duration breakdown in seconds
    cost_estimate: Dict[str, float]  # footage, tts, compute, total USD
    warnings: List[str] = Field(default_factory=list)
    licence_and_rights: Dict[str, Any]
    outputs: Dict[str, VideoOutputInfo]
    validation_results: Dict[str, Any]
