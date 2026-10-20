"""Storyboard Generation Endpoint: Transforms raw brain dumps into polished narration and structured IncuBrix scenes."""
import json
import os
import re
from typing import Any, Dict, List, Optional
import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.exceptions import InvalidInputError
from app.core.logging import logger
from app.services.text_parser import (
    parse_visual_query,
    enforce_keyword_length_constraint,
    BANNED_ABSTRACT_WORDS,
)

router = APIRouter()


class StoryboardRequest(BaseModel):
    raw_text: str = Field(..., min_length=1, description="Raw, unformatted brain dump thoughts or video ideas")
    target_scene_count: int = Field(4, ge=1, le=12, description="Target number of sequential scenes")


class StoryboardScene(BaseModel):
    text: str = Field(..., description="Narration line for this scene")
    visual_query: str = Field(..., description="Concrete, physical visual keywords (2 to 4 words)")
    visual_hint: Optional[str] = Field(None, description="Visual hint for direct IncuBrix engine compatibility")

    def model_post_init(self, __context: Any) -> None:
        if not self.visual_hint:
            self.visual_hint = self.visual_query


class StoryboardResponse(BaseModel):
    polished_narration: str = Field(..., description="Polished, hook-driven spoken narration script")
    scenes: List[StoryboardScene] = Field(..., description="Sequential structured scene breakdown")


STORYBOARD_SYSTEM_PROMPT = (
    "You are an expert video producer, copywriter, and cinematographer. "
    "Your job is to transform raw, unedited brain dumps into a compelling spoken video storyboard.\n\n"
    "TASK:\n"
    "1. Polish the raw input into a concise, natural spoken voiceover script with a strong, curiosity-inducing hook in the first sentence.\n"
    "2. Split the script into sequential scenes.\n"
    "3. For every scene, specify a 'visual_query': describe what physical object, person, or action is visible on screen if a director filmed B-roll for this line.\n\n"
    "CRITICAL CONSTRAINTS FOR visual_query:\n"
    "- Output STRICTLY 2 to 4 concrete physical visual keywords separated by spaces (e.g. 'person typing notebook desk', 'smartphone scrolling close up', 'modern corporate glass office').\n"
    "- BANNED WORDS: Absolutely NEVER use abstract nouns, metaphors, emotions, or subjective/polysemic words (e.g., ban 'consistency', 'scale', 'success', 'future', 'quality', 'value', 'growth', 'resilience', 'freedom').\n\n"
    "OUTPUT FORMAT: Return a valid JSON object matching:\n"
    "{\n"
    '  "polished_narration": "Full polished spoken narration script...",\n'
    '  "scenes": [\n'
    '    {"text": "Scene 1 spoken line...", "visual_query": "person typing notebook desk"},\n'
    '    {"text": "Scene 2 spoken line...", "visual_query": "smartphone scrolling close up"}\n'
    "  ]\n"
    "}"
)


def call_llm_storyboard(raw_text: str, target_scene_count: int, timeout: float = 6.0) -> Optional[Dict[str, Any]]:
    """Attempt LLM completion to generate polished script and scene breakdown."""
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY") or getattr(settings, "LLM_API_KEY", None)
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")

    if not api_key:
        return None

    user_prompt = (
        f"Raw Brain Dump:\n{raw_text}\n\n"
        f"Target Scene Count: {target_scene_count}\n"
        "Generate the polished narration and structured scene JSON:"
    )

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": STORYBOARD_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.3,
                    "response_format": {"type": "json_object"},
                },
            )
            if resp.status_code == 200:
                data = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(data)
                if "polished_narration" in parsed and "scenes" in parsed and isinstance(parsed["scenes"], list):
                    # Sanitize scenes and enforce constraints on visual_query
                    cleaned_scenes = []
                    context = parsed["polished_narration"]
                    for sc in parsed["scenes"]:
                        sc_text = sc.get("text", "").strip()
                        raw_vq = sc.get("visual_query", "")
                        # Validate visual_query against negative constraint and length
                        vq_tokens = raw_vq.split()
                        cleaned_vq = enforce_keyword_length_constraint(vq_tokens)
                        if not cleaned_vq:
                            cleaned_vq = parse_visual_query(sc_text, context=context)
                        cleaned_scenes.append({
                            "text": sc_text,
                            "visual_query": cleaned_vq,
                            "visual_hint": cleaned_vq,
                        })
                    return {
                        "polished_narration": parsed["polished_narration"].strip(),
                        "scenes": cleaned_scenes,
                    }
    except Exception as e:
        logger.debug("LLM storyboard generation failed or timed out: %s", str(e))

    return None


def generate_fallback_storyboard(raw_text: str, target_scene_count: int) -> Dict[str, Any]:
    """Fallback safety layer: Creates a polished hook-driven script and structured scenes with physical visual queries."""
    clean_raw = raw_text.strip()
    
    # Strip colloquial filler openers
    clean_raw = re.sub(r"^(?:so\s+)?basically\s*,?\s*", "", clean_raw, flags=re.IGNORECASE)
    clean_raw = re.sub(r"^(?:i\s+think\s+that|i\s+want\s+to\s+talk\s+about|like\s*,?\s*)", "", clean_raw, flags=re.IGNORECASE)

    # Strip bullet points, numbers, multiple newlines
    lines = [re.sub(r"^[\s*\-•\d.]+", "", line).strip() for line in clean_raw.splitlines() if line.strip()]
    full_cleaned = " ".join(lines) if lines else clean_raw

    # Split into clean sentences
    sentences = [s.strip() for s in re.split(r"[.!?]+", full_cleaned) if s.strip() and len(s.strip()) > 3]

    target_count = max(1, min(target_scene_count, 12))

    # If sentences are fewer than target_count, also split on coordinating conjunctions
    if len(sentences) < target_count:
        expanded: List[str] = []
        for s in sentences:
            parts = [p.strip() for p in re.split(r"\s+(?:and\s+how|and\s+then|and\s+now|and\s+also|because|but|so|while)\s+", s, flags=re.IGNORECASE) if p.strip()]
            if len(parts) > 1 and len(expanded) + len(parts) <= target_count + 1:
                expanded.extend(parts)
            else:
                expanded.append(s)
        sentences = [p for p in expanded if len(p) > 2]

    if not sentences:
        sentences = [clean_raw if clean_raw else "Every great breakthrough begins with a single focused step."]

    # Polish into hook-driven narration
    first_sentence = sentences[0]
    if len(first_sentence.split()) < 5:
        first_sentence = f"Here is what really matters: {first_sentence.lower()}."
    else:
        first_sentence = first_sentence[0].upper() + first_sentence[1:]
        if not first_sentence.endswith((".", "!", "?")):
            first_sentence += "."

    polished_sentences = [first_sentence] + [
        (s[0].upper() + s[1:] + ("." if not s.endswith((".", "!", "?")) else ""))
        for s in sentences[1:]
    ]

    scenes_data: List[Dict[str, str]] = []

    if len(polished_sentences) >= target_count:
        chunk_size = len(polished_sentences) / target_count
        for i in range(target_count):
            start_idx = int(round(i * chunk_size))
            end_idx = int(round((i + 1) * chunk_size))
            scene_text = " ".join(polished_sentences[start_idx:end_idx]).strip()
            if not scene_text and polished_sentences:
                scene_text = polished_sentences[-1]
            scenes_data.append({"text": scene_text})
    else:
        for s in polished_sentences:
            scenes_data.append({"text": s})

        # Fill remaining beats with progressive insights
        progressive_closing_beats = [
            "Consistent daily execution compounds into remarkable outcomes.",
            "Take decisive action today and create your lasting momentum.",
            "Focus on the process and let the results speak for themselves.",
            "Small deliberate improvements create extraordinary value over time.",
        ]
        beat_idx = 0
        while len(scenes_data) < target_count:
            beat_text = progressive_closing_beats[beat_idx % len(progressive_closing_beats)]
            scenes_data.append({"text": beat_text})
            beat_idx += 1

    # Limit to target_count
    scenes_data = scenes_data[:target_count]

    # Generate polished narration string
    polished_narration = " ".join([sc["text"] for sc in scenes_data])

    # Assign concrete physical visual_query for each scene using context-aware disambiguation
    final_scenes = []
    for sc in scenes_data:
        v_query = parse_visual_query(sc["text"], context=polished_narration)
        final_scenes.append({
            "text": sc["text"],
            "visual_query": v_query,
            "visual_hint": v_query,
        })

    return {
        "polished_narration": polished_narration,
        "scenes": final_scenes,
    }


@router.post(
    "/from-brain-dump",
    response_model=StoryboardResponse,
    summary="Transform raw brain dumps into a polished video script and IncuBrix scenes",
    tags=["Storyboard"],
)
async def create_storyboard_from_brain_dump(req: StoryboardRequest) -> StoryboardResponse:
    """Takes raw, unformatted ideas or brain dumps and generates a polished, hook-driven spoken narration
    along with structured IncuBrix scenes with concrete physical visual queries.
    """
    clean_text = req.raw_text.strip()
    if not clean_text:
        raise InvalidInputError("raw_text cannot be empty.")

    # 1. Attempt LLM generation
    result = call_llm_storyboard(clean_text, req.target_scene_count)

    # 2. Fallback safety layer
    if not result:
        result = generate_fallback_storyboard(clean_text, req.target_scene_count)

    scenes_objects = [
        StoryboardScene(
            text=sc["text"],
            visual_query=sc["visual_query"],
            visual_hint=sc.get("visual_hint", sc["visual_query"]),
        )
        for sc in result["scenes"]
    ]

    return StoryboardResponse(
        polished_narration=result["polished_narration"],
        scenes=scenes_objects,
    )
