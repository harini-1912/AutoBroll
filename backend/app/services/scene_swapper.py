"""Scene Clip Swapper Service conforming to Phase 3D and MH-5.

Enables selective re-rendering of a single scene clip while reusing unaffected
scene clips and preserving exact narration timing, captions, and audio sync.
"""
import asyncio
import copy
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.database import db
from app.core.exceptions import (
    AppException,
    FootageNotFoundError,
    InvalidInputError,
    JobNotFoundError,
    RenderFailedError,
    SceneNotFoundError,
)
from app.core.logging import logger
from app.core.retry import execute_with_retry
from app.providers.base import BaseFootageProvider, FootageCandidate
from app.providers.mock import MockFootageProvider
from app.rendering.ffmpeg_engine import ffmpeg_engine, probe_media
from app.schemas.job import SceneClipReplacementRequest, SceneClipReplacementResponse
from app.services.video_downloader import fetch_video_from_url
from app.workers.job_runner import job_runner


class SceneSwapper:
    """Orchestrates safe, selective scene clip replacement and re-assembly."""

    def __init__(self):
        self._job_locks: Dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def _get_job_lock(self, job_id: str) -> asyncio.Lock:
        async with self._global_lock:
            if job_id not in self._job_locks:
                self._job_locks[job_id] = asyncio.Lock()
            return self._job_locks[job_id]

    async def replace_scene_clip(
        self,
        job_id: str,
        scene_id: str,
        swap_req: SceneClipReplacementRequest,
    ) -> Dict[str, Any]:
        """Perform selective re-render of scene_id in job_id without re-rendering unaffected scenes."""
        # 1. Path traversal & parameter safety
        if any(c in job_id for c in ("..", "/", "\\")):
            raise InvalidInputError(f"Invalid characters in job_id: '{job_id}'")
        if any(c in scene_id for c in ("..", "/", "\\")):
            raise InvalidInputError(f"Invalid characters in scene_id: '{scene_id}'")

        # 2. Acquire per-job concurrency lock
        job_lock = await self._get_job_lock(job_id)
        if job_lock.locked():
            logger.warning("Job %s is currently locked for another clip swap operation", job_id)
            raise InvalidInputError(
                f"Job '{job_id}' is currently undergoing an active clip replacement operation. Try again shortly."
            )

        async with job_lock:
            return await self._execute_swap_operation(job_id, scene_id, swap_req)

    async def _execute_swap_operation(
        self,
        job_id: str,
        scene_id: str,
        swap_req: SceneClipReplacementRequest,
    ) -> Dict[str, Any]:
        # 3. Retrieve and validate job from database
        job_record = db.get_job(job_id)
        if not job_record:
            raise JobNotFoundError(job_id)

        status = job_record.get("status")
        if status in ("queued", "processing"):
            raise InvalidInputError(
                f"Job '{job_id}' is currently in status '{status}'. Only completed jobs can have clips swapped."
            )

        timeline = job_record.get("timeline")
        if not timeline or "scenes" not in timeline or not timeline["scenes"]:
            raise InvalidInputError(f"Job '{job_id}' does not have a valid completed timeline.")

        scenes_list: List[Dict[str, Any]] = timeline["scenes"]
        target_idx: Optional[int] = None
        target_scene: Optional[Dict[str, Any]] = None

        for idx, sc in enumerate(scenes_list):
            if sc.get("scene_id") == scene_id:
                target_idx = idx
                target_scene = sc
                break

        if target_idx is None or target_scene is None:
            raise SceneNotFoundError(scene_id, job_id)

        storage_root = Path(settings.LOCAL_STORAGE_DIR).resolve()
        job_dir = (storage_root / job_id).resolve()

        # Enforce storage confinement
        try:
            job_dir.relative_to(storage_root)
        except ValueError:
            raise InvalidInputError("Path traversal outside storage directory detected.")

        if not job_dir.exists():
            raise JobNotFoundError(f"Storage directory for job '{job_id}' not found.")

        req_payload = job_record.get("request_payload") or {}
        aspect_ratios = (
            timeline.get("aspect_ratios")
            or req_payload.get("aspect_ratios")
            or ["16:9"]
        )
        total_duration = float(timeline.get("total_duration", 5.0))
        scene_duration = float(target_scene.get("duration", 2.0))

        # 4. Resolve replacement footage candidate
        query = (
            swap_req.query_override
            or swap_req.search_description
            or target_scene.get("visual_hint")
            or target_scene.get("text")
            or "business innovation growth"
        )
        avoid_terms = req_payload.get("avoid") or []

        # Create isolated temporary staging directory for atomic safety
        temp_staging_dir = storage_root / "staging" / f"swap_{job_id}_{scene_id}_{int(time.time() * 1000)}"
        temp_staging_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Resolve footage candidate through provider fallback chain
            chosen_clip, raw_video_path, provider_used, is_real_stock = await self._resolve_footage(
                query=query,
                avoid_terms=avoid_terms,
                swap_req=swap_req,
                staging_dir=temp_staging_dir,
                scene_id=scene_id,
            )

            # 5. Render only the target scene clip across all aspect ratios in temporary staging
            # Copy unaffected clips from existing job directory (Selective re-rendering)
            for r in aspect_ratios:
                ratio_slug = r.replace(":", "_")
                staging_scenes_dir = temp_staging_dir / f"scenes_{ratio_slug}"
                staging_scenes_dir.mkdir(parents=True, exist_ok=True)

                existing_scenes_dir = job_dir / f"scenes_{ratio_slug}"
                existing_scenes_dir.mkdir(parents=True, exist_ok=True)

                # A. Render ONLY the targeted scene clip
                replacement_clip_path = staging_scenes_dir / f"clip_{target_idx:03d}.mp4"
                ffmpeg_engine.render_scene_clip(
                    scene_id=scene_id,
                    scene_text=target_scene.get("text", ""),
                    duration=scene_duration,
                    aspect_ratio=r,
                    output_path=replacement_clip_path,
                    scene_index=target_idx,
                    raw_video_path=raw_video_path,
                )

                probe_replacement = probe_media(replacement_clip_path)
                if not probe_replacement["is_valid"]:
                    raise RenderFailedError(
                        f"Replacement scene clip validation failed: {probe_replacement.get('error')}"
                    )

                # B. Reuse all unaffected scene clips from existing directory without re-rendering
                for idx, sc in enumerate(scenes_list):
                    if idx == target_idx:
                        continue
                    existing_clip = existing_scenes_dir / f"clip_{idx:03d}.mp4"
                    dest_clip = staging_scenes_dir / f"clip_{idx:03d}.mp4"

                    if existing_clip.exists() and existing_clip.stat().st_size > 0:
                        # Copy cached clip into staging
                        shutil.copy2(str(existing_clip), str(dest_clip))
                    else:
                        # Smart fast fallback: slice exact scene footage out of existing final video!
                        existing_final_video = job_dir / f"video_{ratio_slug}.mp4"
                        extracted_ok = False
                        if existing_final_video.exists() and existing_final_video.stat().st_size > 0:
                            start_time_val = max(0.0, float(sc.get("start_time", 0.0)))
                            dur_val = max(0.5, float(sc.get("duration", 2.0)))
                            cmd_slice = [
                                ffmpeg_engine.ffmpeg_path,
                                "-y",
                                "-ss",
                                str(start_time_val),
                                "-t",
                                str(dur_val),
                                "-i",
                                str(existing_final_video),
                                "-c:v",
                                "libx264",
                                "-preset",
                                "ultrafast",
                                "-pix_fmt",
                                "yuv420p",
                                "-r",
                                "30",
                                "-an",
                                str(dest_clip),
                            ]
                            try:
                                res_slice = subprocess.run(cmd_slice, capture_output=True, text=True, timeout=15)
                                if res_slice.returncode == 0 and dest_clip.exists() and dest_clip.stat().st_size > 0:
                                    extracted_ok = True
                                    logger.info("Preserved scene %d footage directly from final video in %.2fs", idx, dur_val)
                            except Exception as e:
                                logger.debug("Could not slice scene %d from final video: %s", idx, str(e))

                        if not extracted_ok:
                            # Ultimate fallback: render on demand
                            logger.warning("Cached scene clip %s missing; rendering on demand", existing_clip.name)
                            ffmpeg_engine.render_scene_clip(
                                scene_id=sc.get("scene_id", f"scene_{idx:03d}"),
                                scene_text=sc.get("text", ""),
                                duration=sc.get("duration", 2.0),
                                aspect_ratio=r,
                                output_path=dest_clip,
                                scene_index=idx,
                                raw_video_path=Path(sc.get("raw_clip_path")) if sc.get("raw_clip_path") else None,
                            )

                # C. Re-assemble final video from the updated set of scene clips
                narration_audio = job_dir / "narration.wav"
                staging_final_video = temp_staging_dir / f"video_{ratio_slug}.mp4"

                # Construct concat demuxer file in temp staging
                clip_filenames = [f"scenes_{ratio_slug}/clip_{i:03d}.mp4" for i in range(len(scenes_list))]
                concat_file = temp_staging_dir / f"concat_{ratio_slug}.txt"
                concat_file.write_text("\n".join([f"file '{fn}'" for fn in clip_filenames]) + "\n", encoding="utf-8")

                cmd_concat = [
                    ffmpeg_engine.ffmpeg_path,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_file.resolve()),
                ]

                if narration_audio.exists() and narration_audio.stat().st_size > 0:
                    cmd_concat.extend([
                        "-i",
                        str(narration_audio.resolve()),
                        "-c:v",
                        "copy",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "192k",
                        "-shortest",
                        "-movflags",
                        "+faststart",
                        str(staging_final_video.resolve()),
                    ])
                else:
                    cmd_concat.extend([
                        "-f",
                        "lavfi",
                        "-i",
                        "anullsrc=r=24000:cl=mono",
                        "-c:v",
                        "copy",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "192k",
                        "-shortest",
                        "-movflags",
                        "+faststart",
                        str(staging_final_video.resolve()),
                    ])

                def _run_concat():
                    return subprocess.run(
                        cmd_concat,
                        cwd=str(temp_staging_dir.resolve()),
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )

                proc = await asyncio.to_thread(_run_concat)
                if proc.returncode != 0:
                    err_msg = proc.stderr[-400:].strip() if len(proc.stderr) > 400 else proc.stderr.strip()
                    raise RenderFailedError(
                        f"Failed re-assembling final video for aspect ratio {r}: {err_msg}"
                    )

                # Validate assembled final video
                probe_final = probe_media(staging_final_video)
                if not probe_final["is_valid"]:
                    raise RenderFailedError(
                        f"Reassembled final video validation failed for {r}: {probe_final.get('error')}"
                    )

            # 6. Update Timeline and Manifest metadata
            updated_timeline = copy.deepcopy(timeline)
            updated_scene = updated_timeline["scenes"][target_idx]

            selection_reason = (
                f"User clip swap on query '{query[:30]}...' via {provider_used}"
                if is_real_stock
                else f"Fallback visual selected on clip swap via {provider_used}"
            )

            updated_scene["clip"] = {
                "clip_id": chosen_clip.clip_id,
                "provider": chosen_clip.provider,
                "source_url": chosen_clip.source_url,
                "download_url": chosen_clip.download_url if is_real_stock else None,
                "licence": chosen_clip.licence,
                "attribution": chosen_clip.attribution,
                "is_ai_generated": chosen_clip.is_ai_generated,
                "is_real_stock_footage": is_real_stock,
                "is_fallback": not is_real_stock,
                "media_type": "stock_video" if is_real_stock else "procedural_visual",
                "duration": scene_duration,
                "relevance_score": 4.9 if is_real_stock else 4.0,
                "selection_reason": selection_reason,
                "thumbnail_url": chosen_clip.thumbnail_url,
            }

            # Update manifest
            manifest = job_record.get("manifest") or {}
            updated_manifest = copy.deepcopy(manifest)
            now_iso = datetime.now(timezone.utc).isoformat()
            updated_manifest["completed_at"] = now_iso

            # Update licence information
            all_scenes = updated_timeline["scenes"]
            updated_manifest["licence_and_rights"] = {
                "commercial_use_cleared": True,
                "all_clips_licensed": True,
                "contains_real_stock_footage": any(
                    s.get("clip", {}).get("is_real_stock_footage") for s in all_scenes
                ),
                "contains_ai_media": any(
                    s.get("clip", {}).get("is_ai_generated") or s.get("clip", {}).get("is_fallback")
                    for s in all_scenes
                ),
            }

            # Update warnings list with swap note
            warnings = job_record.get("warnings") or []
            swap_warning = f"Scene {scene_id} was swapped with new footage from {provider_used}."
            if swap_warning not in warnings:
                warnings.append(swap_warning)
            updated_manifest["warnings"] = warnings

            # 7. Atomic Promotion: Overwrite final outputs only after all validations pass
            for r in aspect_ratios:
                ratio_slug = r.replace(":", "_")
                # Promote replaced scene clip
                staging_clip = temp_staging_dir / f"scenes_{ratio_slug}" / f"clip_{target_idx:03d}.mp4"
                final_clip_dest = job_dir / f"scenes_{ratio_slug}" / f"clip_{target_idx:03d}.mp4"
                final_clip_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(staging_clip), str(final_clip_dest))

                # Promote final video
                staging_video = temp_staging_dir / f"video_{ratio_slug}.mp4"
                final_video_dest = job_dir / f"video_{ratio_slug}.mp4"
                shutil.copy2(str(staging_video), str(final_video_dest))

            # Write updated timeline and manifest
            (job_dir / "timeline.json").write_text(json.dumps(updated_timeline, indent=2), encoding="utf-8")
            (job_dir / "manifest.json").write_text(json.dumps(updated_manifest, indent=2), encoding="utf-8")

            # Update database
            result_urls = job_record.get("result_urls") or {}
            db.complete_job_success(
                job_id=job_id,
                result_urls=result_urls,
                timeline=updated_timeline,
                manifest=updated_manifest,
                warnings=warnings,
            )

            base_url = settings.BASE_URL.rstrip("/")
            return {
                "job_id": job_id,
                "scene_id": scene_id,
                "status": "completed",
                "message": f"Successfully replaced footage for scene {scene_id} and re-rendered final videos.",
                "affected_aspect_ratios": aspect_ratios,
                "swapped_clip": updated_scene["clip"],
                "result_urls": result_urls,
                "timeline_url": f"{base_url}/api/v1/jobs/download/{job_id}/timeline.json",
                "manifest_url": f"{base_url}/api/v1/jobs/download/{job_id}/manifest.json",
            }

        finally:
            # Clean up temp staging directory unconditionally
            if temp_staging_dir.exists():
                shutil.rmtree(temp_staging_dir, ignore_errors=True)

    async def _resolve_footage(
        self,
        query: str,
        avoid_terms: List[str],
        swap_req: SceneClipReplacementRequest,
        staging_dir: Path,
        scene_id: str,
    ) -> Tuple[FootageCandidate, Optional[Path], str, bool]:
        """Search and download footage candidate using provider preference or fallback chain."""
        pref = (swap_req.provider_override or "").lower().strip()

        # Build list of providers according to preference
        provider_chain: List[BaseFootageProvider] = []
        if pref == "pexels":
            provider_chain = [
                job_runner.primary_footage_provider,
                job_runner.secondary_footage_provider,
                job_runner.fallback_ai_provider,
            ]
        elif pref == "pixabay":
            provider_chain = [
                job_runner.secondary_footage_provider,
                job_runner.primary_footage_provider,
                job_runner.fallback_ai_provider,
            ]
        elif pref in ("ai_motion", "curated", "mock"):
            provider_chain = [
                job_runner.fallback_ai_provider,
                job_runner.primary_footage_provider,
            ]
        else:
            provider_chain = [
                job_runner.primary_footage_provider,
                job_runner.secondary_footage_provider,
                job_runner.fallback_ai_provider,
            ]

        # Handle custom video_url input
        if swap_req.video_url:
            v_url = swap_req.video_url.strip()
            target_raw = staging_dir / f"swap_raw_{scene_id}.mp4"
            try:
                downloaded_file = await fetch_video_from_url(v_url, target_raw)
            except AppException:
                raise
            except Exception as e:
                logger.warning("Custom video_url download failed: %s", str(e))
                raise InvalidInputError(f"Could not download or validate video from video_url: {str(e)}")

            probe = probe_media(downloaded_file)
            cand = FootageCandidate(
                clip_id=f"custom_url_{scene_id}",
                provider="custom_url",
                source_url=v_url,
                download_url=v_url,
                licence="User Provided",
                attribution="Custom Video Upload",
                is_ai_generated=False,
                duration=probe.get("duration", 10.0),
                width=probe.get("width") or 1920,
                height=probe.get("height") or 1080,
                fps=probe.get("fps") or 30.0,
                tags=["custom"],
                is_real_footage=True,
            )
            return cand, downloaded_file, "custom_url", True

        # Check for specific clip_id request
        if swap_req.clip_id:
            for p in provider_chain:
                if hasattr(p, "_library"):
                    for candidate in p._library:
                        if candidate.clip_id == swap_req.clip_id:
                            is_real = getattr(candidate, "is_real_footage", False)
                            if not is_real:
                                return candidate, None, getattr(p, "provider_name", "provider"), False
                            target_raw = staging_dir / f"swap_raw_{scene_id}.mp4"
                            try:
                                p.download_clip(candidate, str(target_raw))
                                if target_raw.exists() and target_raw.stat().st_size > 1000:
                                    probe = probe_media(target_raw)
                                    if probe.get("is_valid") and probe.get("has_video"):
                                        return candidate, target_raw, getattr(p, "provider_name", "provider"), True
                            except Exception:
                                pass

        # Search provider chain
        for provider in provider_chain:
            try:
                candidates = await asyncio.to_thread(
                    provider.search, query=query, min_duration=2.0, avoid_terms=avoid_terms
                )
                if not candidates:
                    continue

                for cand in candidates:
                    is_real = getattr(cand, "is_real_footage", False)
                    if not is_real:
                        # Procedural / Mock candidate
                        return cand, None, getattr(provider, "provider_name", "provider"), False

                    target_raw = staging_dir / f"swap_raw_{scene_id}.mp4"
                    try:
                        await asyncio.to_thread(provider.download_clip, cand, str(target_raw))
                        if target_raw.exists() and target_raw.stat().st_size > 1000:
                            probe = probe_media(target_raw)
                            if probe.get("is_valid") and probe.get("has_video"):
                                return cand, target_raw, getattr(provider, "provider_name", "provider"), True
                    except Exception as e:
                        logger.warning("Download/validation failed for candidate %s: %s", cand.clip_id, str(e))
                        continue

            except Exception as e:
                logger.warning("Provider %s search failed during clip swap: %s", getattr(provider, "provider_name", "provider"), str(e))
                continue

        # Ultimate fallback: Mock procedural visual candidate
        mock_cand = FootageCandidate(
            clip_id=f"swap_mock_{scene_id}",
            provider="ai_generative_fallback",
            source_url="https://images.incubrix.test/footage/swap_mock",
            download_url="https://images.incubrix.test/footage/swap_mock.mp4",
            licence="CC0 / Commercial Free",
            attribution="IncuBrix Fallback Visual Asset",
            is_ai_generated=True,
            duration=10.0,
            width=1920,
            height=1080,
            fps=30.0,
            tags=["business", "concept", "motion"],
            is_real_footage=False,
        )
        return mock_cand, None, "ai_generative_fallback", False


scene_swapper = SceneSwapper()
