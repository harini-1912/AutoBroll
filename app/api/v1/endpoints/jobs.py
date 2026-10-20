"""B-Roll Video Jobs Endpoints conforming to Integration Standard #2 & #3."""
import hashlib
import json
import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, Request, Response, UploadFile, status
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
    SceneClipReplacementRequest,
    SceneClipReplacementResponse,
)
from app.services.scene_swapper import scene_swapper
from app.storage import get_storage
from app.workers.job_runner import job_runner

router = APIRouter(tags=["Jobs"])

async def _dispatch_job_background(job_id: str) -> None:
    """Async background task dispatch that executes on the main event loop."""
    job_runner.enqueue_job(job_id)

@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobResponse,
    summary="Create B-Roll Video Job (JSON Payload)",
)
async def create_job_json(
    request: Request,
    job_req: JobCreateRequest,
    background_tasks: BackgroundTasks,
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

    # Enqueue background execution via FastAPI's BackgroundTasks so HTTP response completes immediately
    background_tasks.add_task(_dispatch_job_background, job_id)

    job_record = db.get_job(job_id)
    return _build_job_response(job_record)

@router.post(
    "/upload",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobResponse,
    summary="Create B-Roll Video Job with Audio File Upload",
)
async def create_job_upload(
    background_tasks: BackgroundTasks,
    audio_file: UploadFile = File(...),
    voice: str = Form("en-IN-female"),
    aspect_ratios: str = Form("16:9,9:16"),
    style: str = Form("bright, modern, real-world footage"),
    avoid: Optional[str] = Form(None),
    script: Optional[str] = Form(None),
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
    local_path = (Path(settings.LOCAL_STORAGE_DIR) / rel_path).resolve()

    ratios_list = [r.strip() for r in aspect_ratios.split(",") if r.strip()]
    avoid_list = [a.strip() for a in avoid.split(",") if a.strip()] if avoid else []

    req_dict = {
        "audio_url": saved_url,
        "audio_local_path": str(local_path),
        "script": script.strip() if script else None,
        "scenes": None,
        "voice": voice,
        "aspect_ratios": ratios_list,
        "style": style,
        "avoid": avoid_list,
        "captions": {"burn_in": burn_in_captions, "srt": True},
    }

    db.create_job(job_id=job_id, request_data=req_dict, idempotency_key=idempotency_key)
    # Enqueue background execution via FastAPI's BackgroundTasks so HTTP response completes immediately
    background_tasks.add_task(_dispatch_job_background, job_id)

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

@router.put(
    "/{job_id}/scenes/{scene_id}/clip",
    status_code=status.HTTP_200_OK,
    response_model=SceneClipReplacementResponse,
    summary="Replace scene clip with selective re-rendering (MH-5)",
)
async def replace_scene_clip(
    job_id: str,
    scene_id: str,
    swap_req: SceneClipReplacementRequest,
):
    """Replace a single scene's clip and selectively re-render final videos."""
    result = await scene_swapper.replace_scene_clip(job_id, scene_id, swap_req)
    return SceneClipReplacementResponse(**result)


@router.post(
    "/{job_id}/scenes/{scene_id}/swap",
    status_code=status.HTTP_200_OK,
    response_model=SceneClipReplacementResponse,
    summary="Swap a scene clip with fast selective re-render (Backwards compatible)",
)
async def swap_scene_clip(
    job_id: str,
    scene_id: str,
    swap_req: ClipSwapRequest,
):
    """Backwards-compatible endpoint to replace a single scene's clip."""
    replacement_req = SceneClipReplacementRequest(
        clip_id=swap_req.clip_id,
        query_override=swap_req.query_override,
        search_description=swap_req.search_description,
        provider_override=swap_req.provider_override,
        video_url=swap_req.video_url,
    )
    result = await scene_swapper.replace_scene_clip(job_id, scene_id, replacement_req)
    return SceneClipReplacementResponse(**result)

@router.get(
    "/download/{job_id}/{filename}",
    summary="Download Generated Asset or Metadata",
)
@router.get(
    "/artifacts/{job_id}/{filename}",
    summary="Download Generated Asset or Metadata (Artifact alias)",
    include_in_schema=False,
)
@router.get(
    "/{job_id}/artifacts/{filename}",
    summary="Download Generated Asset or Metadata (Standard artifact alias)",
    include_in_schema=False,
)
def download_asset(
    job_id: str,
    filename: str,
    preview: Optional[str] = None,
    inline: Optional[str] = None,
):
    """Serve output video, SRT, timeline, or manifest file with strict containment and stream support."""
    import shutil

    # Validate job_id to prevent path traversal
    if ".." in job_id or "/" in job_id or "\\" in job_id:
        raise InvalidInputError(f"Invalid characters in job_id: '{job_id}'")

    raw_name = Path(filename).name
    if not raw_name or raw_name in (".", "..") or ".." in filename:
        raise InvalidInputError(f"Invalid filename: '{filename}'")

    # Map common asset keys without extension to disk filename
    key_to_file = {
        "video_16_9": "video_16_9.mp4",
        "video_9_16": "video_9_16.mp4",
        "video_1_1": "video_1_1.mp4",
        "captions_srt": "captions.srt",
        "timeline_json": "timeline.json",
        "manifest_json": "manifest.json",
    }
    safe_name = key_to_file.get(raw_name, raw_name)

    storage_root = Path(settings.LOCAL_STORAGE_DIR).resolve()
    target_file = (storage_root / job_id / safe_name).resolve()

    # Enforce directory confinement to output directory
    try:
        target_file.relative_to(storage_root)
    except ValueError:
        raise InvalidInputError("Path traversal outside storage directory detected.")

    # Determine asset_key, media_type, and attachment headers
    is_preview = preview in ("1", "true") or inline in ("1", "true")

    if safe_name.endswith(".mp4") or raw_name.startswith("video_"):
        asset_key = raw_name[:-4] if raw_name.endswith(".mp4") else raw_name
        download_filename = f"{asset_key}.mp4"
        media_type = "video/mp4"
    elif safe_name.endswith(".json"):
        download_filename = safe_name
        media_type = "application/json"
    elif safe_name.endswith(".srt"):
        download_filename = safe_name
        media_type = "text/plain; charset=utf-8"
    else:
        download_filename = safe_name
        media_type = "application/octet-stream"

    disposition = "inline" if is_preview else f'attachment; filename="{download_filename}"'
    headers = {
        "Content-Disposition": disposition,
        "Accept-Ranges": "bytes",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Content-Disposition, Content-Length, Accept-Ranges",
    }

    if not target_file.exists():
        # Fallback 1: check ARTIFACTS_DIR / job_id
        artifacts_root = Path(settings.ARTIFACTS_DIR).resolve()
        candidate = (artifacts_root / job_id / safe_name).resolve()
        if candidate.exists():
            target_file = candidate
        else:
            # Fallback 2: Check staging folders job_* in ARTIFACTS_DIR
            for s_dir in artifacts_root.glob("job_*"):
                if s_dir.is_dir():
                    cand_file = s_dir / safe_name
                    if cand_file.exists():
                        mf = s_dir / "manifest.json"
                        tf = s_dir / "timeline.json"
                        matched = False
                        if mf.exists():
                            try:
                                if json.loads(mf.read_text(encoding="utf-8")).get("job_id") == job_id:
                                    matched = True
                            except Exception:
                                pass
                        if not matched and tf.exists():
                            try:
                                if json.loads(tf.read_text(encoding="utf-8")).get("job_id") == job_id:
                                    matched = True
                            except Exception:
                                pass
                        if matched:
                            target_file = cand_file
                            try:
                                (storage_root / job_id).mkdir(parents=True, exist_ok=True)
                                shutil.copy2(str(cand_file), str(storage_root / job_id / safe_name))
                            except Exception:
                                pass
                            break

    if not target_file.exists():
        # Fallback 3: Check storage backend
        storage = get_storage()
        rel_path = f"{job_id}/{safe_name}"
        if storage.exists(rel_path):
            data = storage.get_file_bytes(rel_path)
            return Response(content=data, media_type=media_type, headers=headers)
        raise JobNotFoundError(f"File '{safe_name}' for job '{job_id}' not found.")

    return FileResponse(
        path=str(target_file),
        filename=download_filename,
        media_type=media_type,
        headers=headers,
    )

def _build_job_response(job_dict: Dict[str, Any]) -> JobResponse:
    job_id = job_dict["job_id"]
    result_urls = job_dict.get("result_urls") or {}

    # Calculate real-time output availability on storage
    storage_root = Path(settings.LOCAL_STORAGE_DIR) / job_id
    artifacts_root = Path(settings.ARTIFACTS_DIR) / job_id
    outputs_avail = {}
    for key, url in result_urls.items():
        fname = Path(url).name
        outputs_avail[key] = (storage_root / fname).exists() or (artifacts_root / fname).exists()

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

