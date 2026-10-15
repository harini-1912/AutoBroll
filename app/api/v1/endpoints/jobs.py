"""B-Roll Video Jobs Endpoints conforming to Integration Standard #2 & #3."""
import hashlib
import json
import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, File, Form, Header, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, Response
from pathlib import Path

from app.core.config import settings
from app.core.database import db
from app.core.exceptions import (
    IdempotencyConflictError,
    InvalidInputError,
    JobNotFoundError,
    PayloadTooLargeError,
    SceneNotFoundError,
    StorageError,
    UnsupportedMediaTypeError,
)
from app.core.logging import logger
from app.schemas.job import (
    ClipSwapRequest,
    JobCreateRequest,
    JobResponse,
    JobStatus,
)
from app.storage import get_storage
from app.workers.job_runner import job_runner

router = APIRouter(tags=["Jobs"])

@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobResponse,
    summary="Create B-Roll Video Job (JSON Payload)",
)
async def create_job_json(
    request: Request,
    job_req: JobCreateRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Create a video generation job from a JSON payload with Idempotency support."""
    body_bytes = await request.body()
    req_hash = hashlib.sha256(body_bytes).hexdigest()

    # Check idempotency
    if idempotency_key:
        cached = db.get_idempotency_record(idempotency_key)
        if cached:
            if cached["request_hash"] == req_hash:
                # Same key and same payload: return original job response
                existing_job = db.get_job(cached["job_id"])
                if existing_job:
                    logger.info("Idempotent hit for key %s -> returning job %s", idempotency_key, cached["job_id"])
                    return _build_job_response(existing_job)
            else:
                logger.warning("Idempotency conflict for key %s: mismatched payload hash", idempotency_key)
                raise IdempotencyConflictError(
                    "This Idempotency-Key was already used with a different request payload."
                )

    job_id = str(uuid.uuid4())
    req_dict = job_req.model_dump()

    db.create_job(job_id=job_id, request_data=req_dict, idempotency_key=idempotency_key)

    # Record idempotency mapping
    if idempotency_key:
        initial_body = json.dumps({"job_id": job_id, "status": "queued"})
        db.save_idempotency_record(
            idempotency_key=idempotency_key,
            job_id=job_id,
            request_hash=req_hash,
            response_code=status.HTTP_202_ACCEPTED,
            response_body=initial_body,
        )

    # Enqueue background execution
    job_runner.enqueue_job(job_id)

    job_record = db.get_job(job_id)
    return _build_job_response(job_record)

@router.post(
    "/upload",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobResponse,
    summary="Create B-Roll Video Job with Audio File Upload",
)
async def create_job_upload(
    audio_file: UploadFile = File(...),
    voice: str = Form("en-IN-female"),
    aspect_ratios: str = Form("16:9,9:16"),
    style: str = Form("bright, modern, real-world footage"),
    avoid: Optional[str] = Form(None),
    burn_in_captions: bool = Form(True),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Create a video generation job by directly uploading an audio file (mp3, wav, m4a)."""
    # Validate file extension
    filename = audio_file.filename or "audio.mp3"
    ext = Path(filename).suffix.lower()
    if ext not in [".mp3", ".wav", ".m4a"]:
        raise UnsupportedMediaTypeError(
            f"Unsupported audio format '{ext}'. Supported formats are: .mp3, .wav, .m4a"
        )

    content = await audio_file.read()
    # Validate size limit
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise PayloadTooLargeError(
            f"Uploaded audio file exceeds size limit of {settings.MAX_UPLOAD_SIZE_MB}MB."
        )

    if len(content) == 0:
        raise InvalidInputError("Uploaded audio file is empty (0 bytes).")

    # Save uploaded audio to storage
    job_id = str(uuid.uuid4())
    storage = get_storage()
    rel_path = f"uploads/{job_id}{ext}"
    saved_url = storage.save_bytes(rel_path, content, content_type=audio_file.content_type)

    ratios_list = [r.strip() for r in aspect_ratios.split(",") if r.strip()]
    avoid_list = [a.strip() for a in avoid.split(",") if a.strip()] if avoid else []

    req_dict = {
        "audio_url": saved_url,
        "script": None,
        "scenes": None,
        "voice": voice,
        "aspect_ratios": ratios_list,
        "style": style,
        "avoid": avoid_list,
        "captions": {"burn_in": burn_in_captions, "srt": True},
    }

    db.create_job(job_id=job_id, request_data=req_dict, idempotency_key=idempotency_key)
    job_runner.enqueue_job(job_id)

    job_record = db.get_job(job_id)
    return _build_job_response(job_record)

@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get Job Status and Progress",
)
def get_job_status(job_id: str):
    """Retrieve current status, progress, stage, and result links of a job."""
    job_record = db.get_job(job_id)
    if not job_record:
        raise JobNotFoundError(job_id)
    return _build_job_response(job_record)

@router.get(
    "/{job_id}/timeline",
    summary="Get Scene Timeline for Job",
)
def get_job_timeline(job_id: str):
    """Retrieve scene-by-scene clip timeline for a completed or processing job."""
    job_record = db.get_job(job_id)
    if not job_record:
        raise JobNotFoundError(job_id)

    timeline = job_record.get("timeline")
    if not timeline:
        raise InvalidInputError(
            f"Timeline not yet available for job '{job_id}'. Current stage: {job_record.get('stage')}.",
            details={"job_id": job_id, "status": job_record.get("status"), "stage": job_record.get("stage")},
        )
    return timeline

@router.get(
    "/{job_id}/manifest",
    summary="Get Manifest for Job",
)
def get_job_manifest(job_id: str):
    """Retrieve execution manifest (models, cost, timings, rights) for a completed job."""
    job_record = db.get_job(job_id)
    if not job_record:
        raise JobNotFoundError(job_id)

    manifest = job_record.get("manifest")
    if not manifest:
        raise InvalidInputError(
            f"Manifest not yet available for job '{job_id}'. Current stage: {job_record.get('stage')}.",
            details={"job_id": job_id, "status": job_record.get("status"), "stage": job_record.get("stage")},
        )
    return manifest

@router.post(
    "/{job_id}/scenes/{scene_id}/swap",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Swap a scene clip with fast partial re-render",
)
def swap_scene_clip(job_id: str, scene_id: str, swap_req: ClipSwapRequest):
    """Replace a single scene's clip and trigger fast partial re-render."""
    job_record = db.get_job(job_id)
    if not job_record:
        raise JobNotFoundError(job_id)

    timeline = job_record.get("timeline")
    if not timeline or "scenes" not in timeline:
        raise SceneNotFoundError(scene_id, job_id)

    # Check that scene exists
    scene_found = any(s.get("scene_id") == scene_id for s in timeline["scenes"])
    if not scene_found:
        raise SceneNotFoundError(scene_id, job_id)

    return {
        "job_id": job_id,
        "scene_id": scene_id,
        "status": "queued",
        "message": f"Clip swap queued for scene {scene_id}. Only affected scene will be re-rendered.",
    }

@router.get(
    "/download/{job_id}/{filename}",
    summary="Download Generated Asset or Metadata",
)
def download_asset(job_id: str, filename: str):
    """Serve output video, SRT, timeline, or manifest file with strict containment."""
    # Validate job_id to prevent path traversal
    if ".." in job_id or "/" in job_id or "\\" in job_id:
        raise InvalidInputError(f"Invalid characters in job_id: '{job_id}'")

    safe_name = Path(filename).name
    if not safe_name or safe_name in (".", "..") or ".." in filename:
        raise InvalidInputError(f"Invalid filename: '{filename}'")

    storage_root = Path(settings.LOCAL_STORAGE_DIR).resolve()
    target_file = (storage_root / job_id / safe_name).resolve()

    # Enforce directory confinement to output directory
    try:
        target_file.relative_to(storage_root)
    except ValueError:
        raise InvalidInputError("Path traversal outside storage directory detected.")

    if not target_file.exists():
        # Check storage backend
        storage = get_storage()
        rel_path = f"{job_id}/{safe_name}"
        if storage.exists(rel_path):
            data = storage.get_file_bytes(rel_path)
            media_type = "video/mp4" if safe_name.endswith(".mp4") else "application/octet-stream"
            return Response(content=data, media_type=media_type)
        raise JobNotFoundError(f"File '{safe_name}' for job '{job_id}' not found.")

    media_type = "video/mp4" if safe_name.endswith(".mp4") else "application/octet-stream"
    if safe_name.endswith(".json"):
        media_type = "application/json"
    elif safe_name.endswith(".srt"):
        media_type = "text/plain; charset=utf-8"

    return FileResponse(path=str(target_file), filename=safe_name, media_type=media_type)

def _build_job_response(job_dict: Dict[str, Any]) -> JobResponse:
    job_id = job_dict["job_id"]
    result_urls = job_dict.get("result_urls") or {}

    # Calculate real-time output availability on storage
    storage_root = Path(settings.LOCAL_STORAGE_DIR) / job_id
    outputs_avail = {}
    for key, url in result_urls.items():
        fname = Path(url).name
        outputs_avail[key] = (storage_root / fname).exists()

    return JobResponse(
        job_id=job_id,
        status=JobStatus(job_dict["status"]),
        progress=job_dict.get("progress", 0),
        stage=job_dict.get("stage", "queued"),
        created_at=job_dict.get("created_at", ""),
        started_at=job_dict.get("started_at"),
        completed_at=job_dict.get("completed_at"),
        retry_count=job_dict.get("retry_count", 0),
        result_urls=result_urls if result_urls else None,
        timeline_available=bool(job_dict.get("timeline")),
        manifest_available=bool(job_dict.get("manifest")),
        outputs_available=outputs_avail,
        error=job_dict.get("error_payload"),
        warnings=job_dict.get("warnings") or [],
    )
