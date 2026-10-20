"""Main API Router aggregating all v1 endpoints under /api/v1."""
from fastapi import APIRouter
from app.api.v1.endpoints.jobs import router as jobs_router
from app.api.v1.endpoints.storyboard import router as storyboard_router
from app.api.v1.endpoints.audio import router as audio_router

api_router = APIRouter(prefix="/api/v1")

# Mount jobs router at /broll/jobs, /jobs, and /broll for 100% route compatibility
api_router.include_router(jobs_router, prefix="/broll/jobs", tags=["B-Roll Jobs"])
api_router.include_router(jobs_router, prefix="/jobs", tags=["Jobs"])
api_router.include_router(storyboard_router, prefix="/storyboard", tags=["Storyboard"])
api_router.include_router(audio_router, prefix="/audio", tags=["Audio"])
api_router.include_router(audio_router, prefix="/broll/audio", tags=["B-Roll Audio"])

