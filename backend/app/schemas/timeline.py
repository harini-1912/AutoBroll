"""Timeline Schema for scene-by-scene clip metadata."""
from typing import List, Optional
from pydantic import BaseModel, Field

class SceneClip(BaseModel):
    clip_id: str
    provider: str
    source_url: str
    licence: str
    attribution: str
    is_ai_generated: bool = False
    duration: float
    relevance_score: float = Field(..., ge=1.0, le=5.0)
    selection_reason: str
    thumbnail_url: Optional[str] = None

class SceneTimelineItem(BaseModel):
    scene_id: str
    scene_index: int
    text: str
    visual_hint: Optional[str] = None
    start_time: float
    end_time: float
    duration: float
    clip: SceneClip
    warnings: List[str] = Field(default_factory=list)

class Timeline(BaseModel):
    job_id: str
    total_scenes: int
    total_duration: float
    scenes: List[SceneTimelineItem]
