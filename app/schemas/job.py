"""Job Schemas for IncuBrix API."""
from enum import Enum
from typing import Any, Dict, List, Optional
# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field, model_validator

class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    RUNNING = "running"
    COMPLETED = "completed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

class SceneInput(BaseModel):
    text: str = Field(..., min_length=1, description="Narration line for this scene")
    visual_hint: Optional[str] = Field(None, description="Visual hint or imagery description")

class CaptionsConfig(BaseModel):
    burn_in: bool = Field(True, description="Whether to burn captions into the video")
    srt: bool = Field(True, description="Whether to output standalone .srt file")

class JobCreateRequest(BaseModel):
    audio_url: Optional[str] = Field(None, description="Direct URL to voiceover audio (mp3, wav, m4a)")
    script: Optional[str] = Field(None, description="Plain text narration script")
    scenes: Optional[List[SceneInput]] = Field(None, description="IncuBrix scene list")
    voice: str = Field("en-IN-female", description="Voice identifier for text-to-speech")
    aspect_ratios: List[str] = Field(default_factory=lambda: ["16:9", "9:16"], description="Output aspect ratios")
    style: str = Field("bright, modern, real-world footage", description="Visual styling prompt/hint")
    avoid: List[str] = Field(default_factory=list, description="Content or objects to avoid")
    captions: CaptionsConfig = Field(default_factory=CaptionsConfig, description="Caption settings")
    callback_url: Optional[str] = Field(None, description="Webhook callback URL")

    @model_validator(mode="after")
    def validate_inputs(self) -> "JobCreateRequest":
        has_audio = bool(self.audio_url and self.audio_url.strip())
        has_script = bool(self.script and self.script.strip())
        has_scenes = bool(self.scenes and len(self.scenes) > 0)

        count = sum([has_audio, has_script, has_scenes])
        if count == 0:
            raise ValueError("At least one input must be provided: 'audio_url', 'script', or 'scenes'.")

        # Validate aspect ratios
        valid_ratios = {"16:9", "9:16", "1:1"}
        for r in self.aspect_ratios:
            if r not in valid_ratios:
                raise ValueError(f"Invalid aspect ratio '{r}'. Allowed values are: 16:9, 9:16, 1:1.")

        return self

class ClipSwapRequest(BaseModel):
    scene_id: Optional[str] = Field(None, description="ID of the scene whose clip is to be swapped")
    clip_id: Optional[str] = Field(None, description="Specific candidate clip ID to use")
    query_override: Optional[str] = Field(None, description="Override query for footage search")
    search_description: Optional[str] = Field(None, description="Alternative search prompt or visual description")
    provider_override: Optional[str] = Field(None, description="Provider preference ('pexels', 'pixabay', 'ai_motion', 'curated', 'mock')")
    video_url: Optional[str] = Field(None, description="Direct URL to external video footage clip")

class SceneClipReplacementRequest(BaseModel):
    clip_id: Optional[str] = Field(None, description="Specific candidate clip ID to use")
    query_override: Optional[str] = Field(None, description="Override query for footage search")
    search_description: Optional[str] = Field(None, description="Alternative search prompt or visual description")
    provider_override: Optional[str] = Field(None, description="Provider preference ('pexels', 'pixabay', 'ai_motion', 'curated', 'mock')")
    video_url: Optional[str] = Field(None, description="Direct URL to external video footage clip")

class SceneClipReplacementResponse(BaseModel):
    job_id: str
    scene_id: str
    status: str = Field("completed", description="Status of the replacement operation")
    message: str
    affected_aspect_ratios: List[str]
    swapped_clip: Optional[Dict[str, Any]] = None
    result_urls: Optional[Dict[str, str]] = None
    timeline_url: Optional[str] = None
    manifest_url: Optional[str] = None

class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: int = Field(0, ge=0, le=100)
    stage: str = Field("queued", description="Current pipeline stage")
    created_at: str
    completed_at: Optional[str] = None
    result_urls: Optional[Dict[str, str]] = None
    error: Optional[Dict[str, Any]] = None
    warnings: List[str] = Field(default_factory=list)
