"""Main API Router aggregating all v1 endpoints under /api/v1."""
from fastapi import APIRouter
from app.api.v1.endpoints.jobs import router as jobs_router

api_router = APIRouter(prefix="/api/v1")

# Mount jobs router at both /jobs and /broll/jobs for complete compatibility
api_router.include_router(jobs_router, prefix="", tags=["Jobs"])
api_router.include_router(jobs_router, prefix="/broll", tags=["B-Roll Jobs"])
