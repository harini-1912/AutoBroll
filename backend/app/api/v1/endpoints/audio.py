"""Audio Processing & Transcription Endpoints conforming to Integration Standard #2 & #3."""
import asyncio
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.exceptions import InvalidInputError, PayloadTooLargeError, UnsupportedMediaTypeError
from app.core.logging import logger
from app.services.text_parser import parse_visual_query, extract_script_domain_context
from app.services.transcription import audio_transcriber

router = APIRouter(tags=["Audio"])


class SceneItem(BaseModel):
    text: str = Field(..., description="Narration line for this scene")
    visual_hint: str = Field(..., description="Visual hint keywords for stock footage search")


class TranscribeToScenesResponse(BaseModel):
    raw_transcript: str = Field(..., description="Full extracted speech text")
    scenes: List[SceneItem] = Field(..., description="Segmented scenes with visual hint keywords")


ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


@router.post(
    "/transcribe-to-scenes",
    response_model=TranscribeToScenesResponse,
    summary="Transcribe audio to text and generate segmented IncuBrix scenes",
    status_code=status.HTTP_200_OK,
)
async def transcribe_audio_to_scenes(
    file: Optional[UploadFile] = File(None),
    audio_url: Optional[str] = Form(None),
) -> TranscribeToScenesResponse:
    """Accepts an uploaded audio file (.mp3, .wav, .m4a) or audio URL.
    
    1. Transcribes spoken speech to text using Google Speech Recognition.
    2. Segments the transcript into individual scene sentences.
    3. Generates context-anchored visual hint keywords for each scene.
    4. Returns immediately without triggering video rendering.
    """
    if not file and not audio_url:
        raise InvalidInputError("Please provide an audio file (.mp3, .wav, .m4a) or a valid audio_url.")

    temp_dir = Path("./temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path: Optional[Path] = None

    try:
        if file and file.filename:
            ext = Path(file.filename).suffix.lower()
            if ext not in ALLOWED_AUDIO_EXTENSIONS:
                raise UnsupportedMediaTypeError(
                    f"Unsupported audio format '{ext}'. Allowed formats: {', '.join(sorted(ALLOWED_AUDIO_EXTENSIONS))}"
                )

            tmp_path = temp_dir / f"stt_{uuid.uuid4().hex}{ext}"
            max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
            total_bytes = 0

            with open(tmp_path, "wb") as f:
                while True:
                    chunk = await file.read(65536)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        raise PayloadTooLargeError(
                            f"Uploaded file exceeds maximum permitted size of {settings.MAX_UPLOAD_SIZE_MB}MB."
                        )
                    f.write(chunk)
        else:
            # Download from audio_url
            url = (audio_url or "").strip()
            if not (url.startswith("http://") or url.startswith("https://")):
                raise InvalidInputError(f"Invalid audio URL: '{url}'. Must start with http:// or https://")

            ext_match = re.search(r"\.(mp3|wav|m4a|aac|flac|ogg)", url.lower())
            ext = f".{ext_match.group(1)}" if ext_match else ".mp3"
            tmp_path = temp_dir / f"stt_url_{uuid.uuid4().hex}{ext}"
            max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

            try:
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    async with client.stream("GET", url) as resp:
                        if resp.status_code != 200:
                            raise InvalidInputError(f"Failed to fetch audio from URL: HTTP {resp.status_code}")
                        total_bytes = 0
                        with open(tmp_path, "wb") as f:
                            async for chunk in resp.aiter_bytes():
                                total_bytes += len(chunk)
                                if total_bytes > max_bytes:
                                    raise PayloadTooLargeError(
                                        f"Remote audio exceeds maximum permitted size of {settings.MAX_UPLOAD_SIZE_MB}MB."
                                    )
                                f.write(chunk)
            except Exception as dl_err:
                if isinstance(dl_err, (InvalidInputError, PayloadTooLargeError)):
                    raise
                raise InvalidInputError(f"Could not download audio from URL: {str(dl_err)}")

        if not tmp_path.exists() or tmp_path.stat().st_size == 0:
            raise InvalidInputError("Provided audio is empty.")

        # Run speech-to-text recognition in threadpool to keep event loop non-blocking
        transcript = await asyncio.to_thread(audio_transcriber.transcribe, tmp_path)

        if not transcript or not transcript.strip():
            raw_transcript = "Spoken narration audio track."
            logger.warning("STT returned empty transcript for %s; using placeholder", tmp_path.name)
        else:
            raw_transcript = transcript.strip()

        # Extract domain context for high relevance visual hint parsing
        domain_context = extract_script_domain_context(raw_transcript)
        primary_domain = domain_context.get("primary_domain", "commercial")

        # Split into distinct sentences
        raw_sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', raw_transcript) if s.strip()]
        if not raw_sentences:
            raw_sentences = [s.strip() for s in re.split(r'[.!?]+', raw_transcript) if s.strip()]
        if not raw_sentences:
            raw_sentences = [raw_transcript]

        scenes_list: List[SceneItem] = []
        for sentence in raw_sentences:
            clean_s = sentence.strip()
            if not clean_s:
                continue
            # Generate visual query / hint keywords
            v_hint = parse_visual_query(
                clean_s,
                context=raw_transcript,
                domain_context=domain_context,
                primary_domain=primary_domain,
            )
            scenes_list.append(SceneItem(text=clean_s, visual_hint=v_hint))

        if not scenes_list:
            scenes_list = [SceneItem(text=raw_transcript, visual_hint=parse_visual_query(raw_transcript))]

        return TranscribeToScenesResponse(
            raw_transcript=raw_transcript,
            scenes=scenes_list,
        )

    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
