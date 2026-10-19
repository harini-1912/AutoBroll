"""Accurate Narration and Scene Alignment Service conforming to MH-2."""
import math
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.logging import logger
from app.rendering.ffmpeg_engine import probe_media


class NarrationAligner:
    """Calculates accurate sentence-to-audio timing for scene cuts and subtitles."""

    def __init__(self, ffmpeg_path: Optional[str] = None):
        self.ffmpeg_path = ffmpeg_path or settings.FFMPEG_PATH

    def align_scenes(
        self,
        scenes_input: List[Dict[str, Any]],
        total_duration: float,
        audio_path: Optional[Path] = None,
        tts_timestamps: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Compute sentence boundaries with contiguous, non-overlapping coverage.

        Priority order:
        1. Provider-supplied timestamps (tts_timestamps)
        2. Audio silence detection (audio_silence_alignment)
        3. Proportional pacing estimation (estimated)
        """
        if not scenes_input:
            return []

        total_duration = max(1.0, round(float(total_duration), 2))
        num_scenes = len(scenes_input)

        # -----------------------------------------------------------------
        # Priority 1: Provider-supplied timestamps
        # -----------------------------------------------------------------
        if tts_timestamps and len(tts_timestamps) == num_scenes:
            try:
                aligned = self._align_from_provider_timestamps(scenes_input, tts_timestamps, total_duration)
                if aligned:
                    logger.info("Aligned %d scenes using Priority 1: provider tts_timestamps", len(aligned))
                    return aligned
            except Exception as e:
                logger.warning("Priority 1 tts_timestamps alignment failed: %s; falling back", str(e))

        # -----------------------------------------------------------------
        # Priority 2: Audio silence detection via FFmpeg
        # -----------------------------------------------------------------
        if audio_path and Path(audio_path).exists() and num_scenes > 1:
            try:
                aligned = self._align_from_audio_silence(scenes_input, Path(audio_path), total_duration)
                if aligned:
                    logger.info("Aligned %d scenes using Priority 2: audio_silence_alignment", len(aligned))
                    return aligned
            except Exception as e:
                logger.warning("Priority 2 audio_silence alignment failed: %s; falling back", str(e))

        # -----------------------------------------------------------------
        # Priority 3: Proportional pacing estimation
        # -----------------------------------------------------------------
        logger.info("Aligning %d scenes using Priority 3: proportional pacing estimation", num_scenes)
        return self._align_from_pacing_estimation(scenes_input, total_duration)

    def _align_from_provider_timestamps(
        self,
        scenes_input: List[Dict[str, Any]],
        timestamps: List[Dict[str, Any]],
        total_duration: float,
    ) -> List[Dict[str, Any]]:
        """Validate and normalize provider-supplied timestamps."""
        raw_durations: List[float] = []
        for ts in timestamps:
            st = float(ts.get("start_time", 0.0))
            et = float(ts.get("end_time", st + 1.0))
            dur = max(0.5, et - st)
            raw_durations.append(dur)

        sum_dur = sum(raw_durations)
        scale = total_duration / sum_dur if sum_dur > 0 else 1.0

        scenes_data: List[Dict[str, Any]] = []
        curr_time = 0.0

        for idx, (sc, dur) in enumerate(zip(scenes_input, raw_durations)):
            scaled_dur = round(dur * scale, 2)
            start_t = round(curr_time, 2)
            if idx == len(scenes_input) - 1:
                end_t = total_duration
            else:
                end_t = round(start_t + scaled_dur, 2)
                end_t = min(end_t, total_duration - (len(scenes_input) - 1 - idx) * 0.5)

            actual_dur = round(max(0.5, end_t - start_t), 2)
            curr_time = end_t

            scenes_data.append({
                "scene_id": sc.get("scene_id", f"scene_{idx+1:03d}"),
                "scene_index": idx,
                "text": sc["text"],
                "visual_hint": sc.get("visual_hint"),
                "start_time": start_t,
                "end_time": end_t,
                "duration": actual_dur,
                "alignment_method": "tts_timestamps",
                "confidence": 0.95,
            })

        return self._enforce_boundary_invariants(scenes_data, total_duration)

    def _align_from_audio_silence(
        self,
        scenes_input: List[Dict[str, Any]],
        audio_path: Path,
        total_duration: float,
    ) -> Optional[List[Dict[str, Any]]]:
        """Detect sentence pauses in the audio file using FFmpeg silencedetect."""
        if not self.ffmpeg_path or not Path(self.ffmpeg_path).exists():
            return None

        # Look for acoustic silence >= 200ms at -28dB
        cmd = [
            self.ffmpeg_path,
            "-i",
            str(audio_path),
            "-af",
            "silencedetect=noise=-28dB:d=0.20",
            "-f",
            "null",
            "-",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode != 0:
            return None

        silence_midpoints: List[float] = []
        current_start: Optional[float] = None

        for line in res.stderr.splitlines():
            if "silence_start:" in line:
                m = re.search(r"silence_start:\s*(\d+(?:\.\d+)?)", line)
                if m:
                    current_start = float(m.group(1))
            elif "silence_end:" in line and current_start is not None:
                m = re.search(r"silence_end:\s*(\d+(?:\.\d+)?)", line)
                if m:
                    s_end = float(m.group(1))
                    mid = round((current_start + s_end) / 2.0, 2)
                    if 0.5 < mid < total_duration - 0.5:
                        silence_midpoints.append(mid)
                    current_start = None

        needed_cuts = len(scenes_input) - 1
        if len(silence_midpoints) < needed_cuts:
            # Not enough distinct acoustic pauses found
            return None

        # Select the most evenly spaced candidate silence midpoints
        step = len(silence_midpoints) / (needed_cuts + 1)
        selected_cuts = [silence_midpoints[int((i + 1) * step)] for i in range(needed_cuts)]
        selected_cuts = sorted(list(set(selected_cuts)))

        if len(selected_cuts) != needed_cuts:
            return None

        boundaries = [0.0] + selected_cuts + [total_duration]
        scenes_data: List[Dict[str, Any]] = []

        for idx, sc in enumerate(scenes_input):
            st = round(boundaries[idx], 2)
            et = round(boundaries[idx + 1], 2)
            dur = round(max(0.5, et - st), 2)
            scenes_data.append({
                "scene_id": sc.get("scene_id", f"scene_{idx+1:03d}"),
                "scene_index": idx,
                "text": sc["text"],
                "visual_hint": sc.get("visual_hint"),
                "start_time": st,
                "end_time": et,
                "duration": dur,
                "alignment_method": "audio_silence_alignment",
                "confidence": 0.88,
            })

        return self._enforce_boundary_invariants(scenes_data, total_duration)

    def _align_from_pacing_estimation(
        self,
        scenes_input: List[Dict[str, Any]],
        total_duration: float,
    ) -> List[Dict[str, Any]]:
        """Linguistic pacing model based on word count, syllable weight, and punctuation pauses."""
        num_scenes = len(scenes_input)
        if num_scenes == 1:
            sc = scenes_input[0]
            return [{
                "scene_id": sc.get("scene_id", "scene_001"),
                "scene_index": 0,
                "text": sc["text"],
                "visual_hint": sc.get("visual_hint"),
                "start_time": 0.0,
                "end_time": total_duration,
                "duration": total_duration,
                "alignment_method": "estimated",
                "confidence": 0.70,
            }]

        weights: List[float] = []
        for sc in scenes_input:
            text = sc.get("text", "")
            words = [w for w in re.findall(r"\w+", text)]
            word_count = max(1, len(words))
            char_count = len(text)

            # Extra weight for sentence-ending punctuation (implies natural speech pause)
            pause_bonus = 0.0
            if any(text.endswith(p) for p in [".", "!", "?", "..."]):
                pause_bonus = 0.35
            elif any(text.endswith(p) for p in [",", ";", ":"]):
                pause_bonus = 0.15

            w = (word_count * 0.40) + (char_count * 0.03) + pause_bonus
            weights.append(max(0.5, w))

        total_weight = sum(weights)
        scenes_data: List[Dict[str, Any]] = []
        curr_time = 0.0

        for idx, sc in enumerate(scenes_input):
            prop_dur = (weights[idx] / total_weight) * total_duration
            start_t = round(curr_time, 2)

            if idx == num_scenes - 1:
                end_t = total_duration
            else:
                end_t = round(start_t + prop_dur, 2)
                # Keep headroom for remaining scenes
                end_t = min(end_t, total_duration - (num_scenes - 1 - idx) * 0.5)
                end_t = max(end_t, start_t + 0.5)

            actual_dur = round(max(0.5, end_t - start_t), 2)
            curr_time = end_t

            scenes_data.append({
                "scene_id": sc.get("scene_id", f"scene_{idx+1:03d}"),
                "scene_index": idx,
                "text": sc["text"],
                "visual_hint": sc.get("visual_hint"),
                "start_time": start_t,
                "end_time": end_t,
                "duration": actual_dur,
                "alignment_method": "estimated",
                "confidence": 0.70,
            })

        return self._enforce_boundary_invariants(scenes_data, total_duration)

    def _enforce_boundary_invariants(
        self,
        scenes_data: List[Dict[str, Any]],
        total_duration: float,
    ) -> List[Dict[str, Any]]:
        """Strictly enforce that start >= 0, end > start, start[i+1] == end[i], and end[last] == total_duration."""
        if not scenes_data:
            return []

        scenes_data[0]["start_time"] = 0.0
        for i in range(len(scenes_data) - 1):
            s_curr = scenes_data[i]
            s_next = scenes_data[i + 1]

            # Contiguous connection
            s_curr["end_time"] = max(round(s_curr["start_time"] + 0.5, 2), s_curr["end_time"])
            s_curr["duration"] = round(s_curr["end_time"] - s_curr["start_time"], 2)
            s_next["start_time"] = s_curr["end_time"]

        last = scenes_data[-1]
        last["end_time"] = total_duration
        if last["end_time"] <= last["start_time"]:
            last["start_time"] = max(0.0, round(total_duration - 0.5, 2))
        last["duration"] = round(last["end_time"] - last["start_time"], 2)

        return scenes_data


narration_aligner = NarrationAligner()
