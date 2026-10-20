"""FFmpeg Video Assembly and Rendering Engine conforming to MH-4 and Integration Standards.

Handles:
- Procedural visual scene rendering for deterministic testing
- Multi-scene timeline concatenation
- Narration audio muxing (AAC, zero-drift, synchronized)
- Aspect ratio normalization (16:9 -> 1920x1080, 9:16 -> 1080x1920, 1:1 -> 1080x1080)
- Stream probing and container validation (detecting missing moov atoms and corrupt headers)
"""
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.exceptions import RenderFailedError
from app.core.logging import logger

ASPECT_RATIO_DIMENSIONS: Dict[str, Tuple[int, int]] = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}

# Curated palette of modern, professional theme colors for scene backgrounds
SCENE_PALETTES = [
    "#1e293b",  # Slate 800
    "#0f172a",  # Slate 900
    "#1e1b4b",  # Indigo 950
    "#312e81",  # Indigo 900
    "#0f766e",  # Teal 700
    "#1e3a5f",  # Deep Navy
    "#3b0764",  # Purple 950
    "#172554",  # Blue 950
]


def probe_media(file_path: Path, ffmpeg_path: Optional[str] = None) -> Dict[str, Any]:
    """Inspect a media container using FFmpeg to extract stream metadata and verify validity.

    Detects missing moov atoms, corrupted headers, and dummy mock files.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        return {
            "is_valid": False,
            "has_video": False,
            "has_audio": False,
            "file_size_bytes": 0,
            "error": f"File does not exist: {file_path}",
        }

    file_size = file_path.stat().st_size
    if file_size == 0:
        return {
            "is_valid": False,
            "has_video": False,
            "has_audio": False,
            "file_size_bytes": 0,
            "error": "File is empty (0 bytes)",
        }

    bin_path = ffmpeg_path or settings.FFMPEG_PATH
    if not bin_path or not Path(bin_path).exists():
        # Fallback to system ffmpeg if custom path does not exist
        bin_path = shutil.which("ffmpeg") or bin_path

    try:
        proc = subprocess.run(
            [bin_path, "-i", str(file_path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        stderr = proc.stderr
    except Exception as e:
        return {
            "is_valid": False,
            "has_video": False,
            "has_audio": False,
            "file_size_bytes": file_size,
            "error": f"Failed to execute FFmpeg probe: {str(e)}",
        }

    # Detect unfinalized or dummy MP4 containers missing the moov atom
    if "moov atom not found" in stderr or "Invalid data found" in stderr:
        return {
            "is_valid": False,
            "has_video": False,
            "has_audio": False,
            "file_size_bytes": file_size,
            "error": "Missing moov atom or invalid container header (dummy/corrupted MP4)",
        }

    # Extract Duration: 00:00:01.50
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    duration = 0.0
    if duration_match:
        h, m, s = duration_match.groups()
        duration = round(int(h) * 3600 + int(m) * 60 + float(s), 3)

    # Extract Video Stream: Stream #0:0: Video: h264 ..., 1920x1080 ..., 30 fps
    video_match = re.search(r"Stream.*?Video:\s*(\w+).*?(\d{3,4})x(\d{3,4}).*?(\d+(?:\.\d+)?)\s*fps", stderr)
    has_video = False
    video_codec = None
    width = 0
    height = 0
    fps = 0.0

    if video_match:
        has_video = True
        video_codec = video_match.group(1).lower()
        width = int(video_match.group(2))
        height = int(video_match.group(3))
        fps = float(video_match.group(4))
    else:
        v_alt = re.search(r"Stream.*?Video:\s*(\w+).*?(\d{3,4})x(\d{3,4})", stderr)
        if v_alt:
            has_video = True
            video_codec = v_alt.group(1).lower()
            width = int(v_alt.group(2))
            height = int(v_alt.group(3))
            fps = 30.0

    # Extract Audio Stream: Stream #0:1: Audio: aac ...
    audio_match = re.search(r"Stream.*?Audio:\s*(\w+)", stderr)
    has_audio = False
    audio_codec = None
    if audio_match:
        has_audio = True
        audio_codec = audio_match.group(1).lower()

    resolution = f"{width}x{height}" if width and height else None

    return {
        "is_valid": has_video,
        "has_video": has_video,
        "has_audio": has_audio,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "resolution": resolution,
        "width": width,
        "height": height,
        "fps": fps,
        "duration": duration,
        "file_size_bytes": file_size,
        "error": None if has_video else "No valid video stream identified in file",
    }


class FFmpegEngine:
    """Production-grade FFmpeg video assembly engine for IncuBrix."""

    def __init__(self, ffmpeg_path: Optional[str] = None):
        self.ffmpeg_path = ffmpeg_path or settings.FFMPEG_PATH
        if not self.ffmpeg_path or not Path(self.ffmpeg_path).exists():
            system_ffmpeg = shutil.which("ffmpeg")
            if system_ffmpeg:
                self.ffmpeg_path = system_ffmpeg

    def is_available(self) -> bool:
        """Check whether a working FFmpeg binary is accessible."""
        if not self.ffmpeg_path:
            return False
        try:
            res = subprocess.run([self.ffmpeg_path, "-version"], capture_output=True, timeout=5)
            return res.returncode == 0
        except Exception:
            return False

    def render_scene_clip(
        self,
        scene_id: str,
        scene_text: str,
        duration: float,
        aspect_ratio: str,
        output_path: Path,
        scene_index: int = 0,
        raw_video_path: Optional[Path] = None,
    ) -> Path:
        """Render an individual scene clip using real stock footage or fallback procedural visuals."""
        if not self.is_available():
            raise RenderFailedError(f"FFmpeg binary not accessible at '{self.ffmpeg_path}'.")

        width, height = ASPECT_RATIO_DIMENSIONS.get(aspect_ratio, (1920, 1080))
        duration = max(0.5, round(float(duration), 2))
        color = SCENE_PALETTES[scene_index % len(SCENE_PALETTES)]

        output_path.parent.mkdir(parents=True, exist_ok=True)
        clip_timeout = max(180, int(duration * 25))

        # 1. If raw stock video clip is provided, scale, crop, and normalize to target aspect ratio
        if raw_video_path and Path(raw_video_path).exists() and Path(raw_video_path).stat().st_size > 0:
            scale_filter = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
            cmd_video = [
                self.ffmpeg_path,
                "-y",
                "-threads",
                str(getattr(settings, "FFMPEG_THREADS", 1)),
                "-stream_loop",
                "-1",
                "-i",
                str(raw_video_path),
                "-t",
                str(duration),
                "-vf",
                scale_filter,
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "26",
                "-tune",
                "fastdecode",
                "-pix_fmt",
                "yuv420p",
                "-r",
                "30",
                "-max_muxing_queue_size",
                "512",
                "-bufsize",
                "2M",
                "-maxrate",
                "4M",
                "-an",
                str(output_path),
            ]
            logger.info("Rendering scene clip %s from stock footage %s (%s, %.2fs, timeout=%ds)", scene_id, Path(raw_video_path).name, aspect_ratio, duration, clip_timeout)
            try:
                proc_vid = subprocess.run(cmd_video, capture_output=True, text=True, timeout=clip_timeout)
                if proc_vid.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                    return output_path
                else:
                    logger.warning(
                        "FFmpeg raw video render failed for scene %s (%s); falling back to procedural visual",
                        scene_id,
                        proc_vid.stderr[:120] if proc_vid.stderr else "non-zero returncode",
                    )
            except subprocess.TimeoutExpired:
                logger.warning(
                    "FFmpeg raw video render timed out (%ds) for scene %s; falling back to procedural visual",
                    clip_timeout,
                    scene_id,
                )
            except Exception as exc:
                logger.warning(
                    "FFmpeg raw video render encountered error (%s) for scene %s; falling back to procedural visual",
                    str(exc),
                    scene_id,
                )

        # 2. Procedural visual backdrop fallback
        clean_text = re.sub(r"[^a-zA-Z0-9\s.,!?-]", "", scene_text).strip()
        if not clean_text:
            clean_text = f"Scene {scene_index + 1}"
        clean_text = clean_text[:60]

        escaped_text = clean_text.replace(":", r"\:").replace("'", r"\\\'")
        font_size = 48 if aspect_ratio != "9:16" else 36

        # Try rendering with drawtext overlay
        vf_filter = (
            f"drawtext=text='{escaped_text}':fontsize={font_size}:fontcolor=white:"
            f"x=(w-text_w)/2:y=(h-text_h)/2"
        )
        cmd_with_text = [
            self.ffmpeg_path,
            "-y",
            "-threads",
            str(getattr(settings, "FFMPEG_THREADS", 1)),
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={width}x{height}:r=30:d={duration}",
            "-vf",
            vf_filter,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            "-max_muxing_queue_size",
            "512",
            str(output_path),
        ]

        logger.debug("Rendering scene clip %s with procedural text: %s", scene_id, clean_text)
        try:
            proc = subprocess.run(cmd_with_text, capture_output=True, text=True, timeout=clip_timeout)
            if proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                return output_path
            else:
                logger.warning("drawtext filter failed for scene %s (%s); falling back to clean backdrop", scene_id, proc.stderr[:120] if proc.stderr else "non-zero returncode")
        except Exception as exc:
            logger.warning("drawtext filter error (%s) for scene %s; falling back to clean backdrop", str(exc), scene_id)

        # Fallback cleanly to plain background
        cmd_fallback = [
            self.ffmpeg_path,
            "-y",
            "-threads",
            str(getattr(settings, "FFMPEG_THREADS", 1)),
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={width}x{height}:r=30:d={duration}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            "-max_muxing_queue_size",
            "512",
            str(output_path),
        ]
        try:
            fallback_proc = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=clip_timeout)
            if fallback_proc.returncode != 0:
                raise RenderFailedError(
                    f"FFmpeg failed to render scene {scene_id}: {fallback_proc.stderr[:200]}"
                )
        except Exception as exc:
            raise RenderFailedError(f"FFmpeg fallback failed for scene {scene_id}: {str(exc)}")

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RenderFailedError(f"Rendered scene clip {scene_id} is missing or empty.")

        return output_path

    def assemble_video(
        self,
        scenes_data: List[Dict[str, Any]],
        aspect_ratio: str,
        narration_audio_path: Optional[Path],
        output_path: Path,
        staging_dir: Path,
        reuse_existing_clips: bool = False,
        captions_srt_path: Optional[Path] = None,
        burn_in: bool = False,
        job_start_time: Optional[float] = None,
    ) -> Path:
        """Assemble scene clips into a normalized video and mux the narration audio track."""
        if not self.is_available():
            raise RenderFailedError(f"FFmpeg binary not accessible at '{self.ffmpeg_path}'.")

        ratio_slug = aspect_ratio.replace(":", "_")
        staging_dir = Path(staging_dir).resolve()
        output_path = Path(output_path).resolve()
        if narration_audio_path:
            narration_audio_path = Path(narration_audio_path).resolve()
        scenes_dir = staging_dir / f"scenes_{ratio_slug}"
        scenes_dir.mkdir(parents=True, exist_ok=True)

        # 1. Render all scene video segments (or reuse existing valid clips if reuse_existing_clips is True)
        clip_filenames: List[str] = []
        for idx, sc in enumerate(scenes_data):
            scene_id = sc.get("scene_id", f"scene_{idx:03d}")
            scene_text = sc.get("text", "")
            duration = sc.get("duration", 2.0)
            scene_clip_file = scenes_dir / f"clip_{idx:03d}.mp4"
            raw_clip_path = sc.get("raw_clip_path")

            if not (reuse_existing_clips and scene_clip_file.exists() and scene_clip_file.stat().st_size > 0):
                self.render_scene_clip(
                    scene_id=scene_id,
                    scene_text=scene_text,
                    duration=duration,
                    aspect_ratio=aspect_ratio,
                    output_path=scene_clip_file,
                    scene_index=idx,
                    raw_video_path=Path(raw_clip_path) if raw_clip_path else None,
                )
            else:
                logger.info("Reusing existing scene clip %s (%s) without re-rendering", scene_id, aspect_ratio)

            # Record relative path for safe concat on all platforms
            clip_filenames.append(f"scenes_{ratio_slug}/clip_{idx:03d}.mp4")

        # 2. Verify that every input .mp4 file exists, is valid, and was created during the current job run
        for fname in clip_filenames:
            clip_file = staging_dir / fname
            if not clip_file.exists() or clip_file.stat().st_size == 0:
                raise RenderFailedError(
                    f"Input scene clip '{fname}' is missing or empty before compiling concat list."
                )
            if job_start_time is not None:
                # Verify clip was created during current job execution window (allowing 5s clock skew)
                if clip_file.stat().st_mtime < (job_start_time - 5.0):
                    raise RenderFailedError(
                        f"Input scene clip '{fname}' was not created during the current job run (mtime={clip_file.stat().st_mtime}, job_start={job_start_time})."
                    )

        # 3. Write concat demuxer files inside staging directory (both ratio-specific and concat_list.txt)
        concat_file = staging_dir / f"concat_{ratio_slug}.txt"
        concat_list_file = staging_dir / "concat_list.txt"
        concat_content = "\n".join([f"file '{fname}'" for fname in clip_filenames]) + "\n"
        concat_file.write_text(concat_content, encoding="utf-8")
        concat_list_file.write_text(concat_content, encoding="utf-8")

        # 3. Assemble and mux with narration audio (and burned-in captions if requested)
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-threads",
            str(getattr(settings, "FFMPEG_THREADS", 1)),
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
        ]

        has_audio = narration_audio_path and Path(narration_audio_path).exists() and Path(narration_audio_path).stat().st_size > 0

        if has_audio:
            cmd.extend(["-i", str(narration_audio_path)])
        else:
            # Generate silent audio track for container audio conformance
            cmd.extend([
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=24000:cl=mono",
            ])

        # Check if burned-in subtitles should be applied
        should_burn = False
        srt_filename = None
        if burn_in and captions_srt_path and Path(captions_srt_path).exists() and Path(captions_srt_path).stat().st_size > 0:
            local_srt = staging_dir / "captions.srt"
            if Path(captions_srt_path).resolve() != local_srt.resolve():
                try:
                    shutil.copyfile(captions_srt_path, local_srt)
                except Exception as e:
                    logger.warning("Could not copy captions SRT to staging dir: %s", e)
            if local_srt.exists() and local_srt.stat().st_size > 0:
                should_burn = True
                srt_filename = local_srt.name

        if should_burn:
            if aspect_ratio == "9:16":
                force_style = (
                    "FontName=DejaVu Sans,Arial,sans-serif,FontSize=15,Bold=1,"
                    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H80000000,"
                    "BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=40,MarginL=25,MarginR=25"
                )
            elif aspect_ratio == "1:1":
                force_style = (
                    "FontName=DejaVu Sans,Arial,sans-serif,FontSize=15,Bold=1,"
                    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H80000000,"
                    "BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=30,MarginL=25,MarginR=25"
                )
            else:
                force_style = (
                    "FontName=DejaVu Sans,Arial,sans-serif,FontSize=16,Bold=1,"
                    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H80000000,"
                    "BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=25,MarginL=30,MarginR=30"
                )
            subtitle_filter = f"subtitles={srt_filename}:force_style='{force_style}'"
            logger.info("Assembling video with burned-in subtitles for %s (filter: %s)", aspect_ratio, subtitle_filter)
            cmd.extend([
                "-vf",
                subtitle_filter,
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "26",
                "-tune",
                "fastdecode",
                "-bufsize",
                "2M",
                "-maxrate",
                "4M",
                "-pix_fmt",
                "yuv420p",
                "-r",
                "30",
            ])
        else:
            cmd.extend([
                "-c:v",
                "copy",
            ])

        cmd.extend([
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            "-max_muxing_queue_size",
            "1024",
            str(output_path),
        ])

        assembly_timeout = max(300, int(sum(sc.get("duration", 2.0) for sc in scenes_data) * 10))
        logger.info("Executing video assembly for aspect ratio %s -> %s (burn_in=%s, timeout=%ds)", aspect_ratio, output_path.name, should_burn, assembly_timeout)
        try:
            proc = subprocess.run(cmd, cwd=str(staging_dir), capture_output=True, text=True, timeout=assembly_timeout)
            if proc.returncode != 0:
                err_tail = proc.stderr[-400:].strip() if len(proc.stderr) > 400 else proc.stderr.strip()
                raise RuntimeError(err_tail)
        except (subprocess.TimeoutExpired, Exception) as enc_err:
            # If burned-in encoding failed or timed out unexpectedly, attempt fallback to stream copy
            if should_burn:
                logger.warning("Burned-in subtitle render failed or timed out (%s); falling back to direct stream copy", str(enc_err))
                fallback_cmd = [
                    self.ffmpeg_path,
                    "-y",
                    "-threads",
                    str(getattr(settings, "FFMPEG_THREADS", 1)),
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_file),
                ]
                if has_audio:
                    fallback_cmd.extend(["-i", str(narration_audio_path)])
                else:
                    fallback_cmd.extend(["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono"])
                fallback_cmd.extend([
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-shortest",
                    "-movflags",
                    "+faststart",
                    "-max_muxing_queue_size",
                    "1024",
                    str(output_path),
                ])
                fb_proc = subprocess.run(fallback_cmd, cwd=str(staging_dir), capture_output=True, text=True, timeout=120)
                if fb_proc.returncode != 0:
                    fb_tail = fb_proc.stderr[-400:].strip() if len(fb_proc.stderr) > 400 else fb_proc.stderr.strip()
                    raise RenderFailedError(
                        f"FFmpeg assembly failed for aspect ratio {aspect_ratio}: {fb_tail}"
                    )
            else:
                raise RenderFailedError(
                    f"FFmpeg assembly failed for aspect ratio {aspect_ratio}: {str(enc_err)}"
                )

        # 4. Rigorous validation of the resulting output file
        probe_info = probe_media(output_path, ffmpeg_path=self.ffmpeg_path)
        if not probe_info["is_valid"]:
            raise RenderFailedError(
                f"Rendered video validation failed for aspect ratio {aspect_ratio}: {probe_info.get('error')}"
            )

        logger.info(
            "Successfully assembled valid MP4 %s (%s, %s, %.2fs, %d bytes)",
            output_path.name,
            probe_info.get("resolution"),
            probe_info.get("video_codec"),
            probe_info.get("duration", 0.0),
            probe_info.get("file_size_bytes", 0),
        )
        return output_path


ffmpeg_engine = FFmpegEngine()
