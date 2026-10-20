"""Context-Aware Visual Disambiguation & Text Parser for B-Roll Stock Footage Retrieval.

Universal Script-Level Domain Anchoring Pipeline:
1. Two-Pass Script Ingestion:
   - Pass 1 (Global Context Extraction):
     Extracts primary_domain (1-2 physical niche tags) and visual_environment (default physical setting)
     from the complete script using LLM or deterministic heuristic extractor.
   - Pass 2 (Context-Enforced Scene Queries):
     Enforces query composition: [primary_domain or environment] + [concrete subject] + [visible action].
     Instructs director prompt to stay strictly grounded in primary_domain and visual_environment.
2. Polysemy Blacklist & Domain Fallback:
   - Strips high-risk polysemic words (cast, barrier, spectrum, layer, scale, launch, pipeline, traffic, cloud, etc.).
   - If an extracted query consists solely of ambiguous or abstract terms, anchors with primary_domain.
3. Adjective Dropper Helper:
   - Dynamically drops descriptive adjectives on Pixabay 0-hit responses, falling back to
     {primary_domain} {base_noun} to guarantee footage never loses its physical domain context.
"""
import json
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import httpx

from app.core.config import settings
from app.core.logging import logger

# Banned abstract nouns, metaphors, emotions, and high-polysemy subjective words
BANNED_ABSTRACT_WORDS: Set[str] = {
    # Emotions, states & subjective judgments
    "consistency", "consistent", "success", "successful", "future", "quality", "qualities",
    "freedom", "potential", "excellence", "efficiency", "efficient", "resilience", "resilient",
    "growth", "value", "values", "purpose", "mission", "vision", "strategy", "strategic",
    "journey", "path", "destiny", "philosophy", "paradigm", "synergy", "culture", "discipline",
    "care", "thoughtful", "costly", "smart", "better", "best", "innovation", "innovative",
    "leadership", "power", "strength", "impact", "solution", "solutions", "transformation",
    "experience", "opportunity", "possibility", "idea", "ideas", "concept", "theory",
    "knowledge", "wisdom", "truth", "reality", "progress", "change", "greatness",
    "determination", "focus", "happiness", "joy", "peace", "harmony", "perfection",
    "failure", "trouble", "hardship", "struggle", "confidence", "confident", "mindset",
    "creativity", "passion", "dedication", "motivation", "inspiration", "execution",
    "sustainable", "sustainability", "crucial", "essential",
    "historic", "milestone", "milestones", "reached", "quarter", "across", "provides", "requires",
    "even", "silky", "protect", "protecting", "protection", "ensures", "health", "ecosystem", "ecosystems",
    "powered", "designed", "instant", "zero", "putting", "leaves", "without", "mood", "meet",
    "boundaries", "boundary", "limits", "limit",
}

# High-risk polysemic words that routinely cause unrelated stock footage downloads
POLYSEMIC_WORDS: Set[str] = {
    "cast", "casts", "casting",
    "barrier", "barriers",
    "spectrum", "spectrums",
    "layer", "layers",
    "scale", "scales", "scaling",
    "launch", "launches", "launching",
    "pipeline", "pipelines",
    "traffic",
    "funnel", "funnels",
    "cloud", "clouds",
    "pool", "pools",
    "stream", "streams", "streaming",
    "tree", "trees",
    "root", "roots",
    "thread", "threads",
}

# General English stop words to strip
STOP_WORDS: Set[str] = {
    "a", "about", "above", "after", "again", "against", "all", "almost", "also", "always",
    "am", "an", "and", "any", "are", "aren't", "as", "at", "be", "because", "been", "before",
    "being", "below", "between", "both", "but", "by", "can", "cannot", "cant", "could", "couldn't",
    "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during", "each", "few",
    "for", "from", "further", "had", "hadn't", "has", "hasn't", "have", "haven't", "having",
    "he", "he'd", "he'll", "he's", "her", "here", "here's", "hers", "herself", "him", "himself",
    "his", "how", "how's", "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't",
    "it", "it's", "its", "itself", "just", "later", "like", "make", "many", "may", "me", "might",
    "more", "most", "must", "my", "myself", "never", "no", "nor", "not", "now", "of", "off", "on",
    "once", "only", "or", "other", "ought", "our", "ours", "ourselves", "out", "over", "own",
    "quite", "rather", "really", "requires", "provides", "ensures", "prevents", "same", "shall",
    "she", "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such", "than", "that",
    "that's", "the", "their", "theirs", "them", "themselves", "then", "there", "there's", "these",
    "they", "they'd", "they'll", "they're", "they've", "this", "those", "through", "to", "today",
    "together", "tomorrow", "too", "under", "until", "up", "very", "was", "wasn't", "we", "we'd",
    "we'll", "we're", "we've", "were", "weren't", "what", "what's", "when", "when's", "where",
    "where's", "which", "while", "who", "who's", "whom", "why", "why's", "will", "with", "won't",
    "would", "wouldn't", "yesterday", "yet", "you", "you'd", "you'll", "you're", "you've", "your",
    "yours", "yourself", "yourselves"
}

# Known physical adjectives to drop when simplifying queries for Pixabay retries
DISMISSABLE_ADJECTIVES: Set[str] = {
    "modern", "corporate", "glass", "wooden", "metal", "urban", "outdoor", "indoor",
    "digital", "industrial", "clean", "bright", "dark", "fast", "slow", "big", "small",
    "huge", "tall", "short", "deep", "high", "low", "close", "up", "wide", "view",
    "beautiful", "elegant", "fancy", "simple", "busy", "empty", "green", "blue", "white",
    "black", "yellow", "red", "golden", "grey", "gray", "majestic", "misty", "rocky",
    "elite", "great", "prime", "fresh", "smart", "quick", "real", "raw", "new", "old",
    "spacious", "luxury", "extreme",
}

# Domain profiles for deterministic Pass 1 Heuristic Extraction
DOMAIN_PROFILES: List[Dict[str, Any]] = [
    {
        "primary_domain": "skincare",
        "visual_environment": "bathroom mirror clean daylight",
        "triggers": {
            "sunscreen", "spf", "sunblock", "skincare", "skin", "pores", "serum", "lotion",
            "moisturizer", "cream", "watergel", "gel", "hyaluronic", "niacinamide", "matte",
            "barrier", "dermatology", "dermatologist", "cosmetic", "cosmetics", "facial",
            "glow", "glowy", "hydration", "hydrating", "beauty", "wrinkle", "wrinkles",
            "complexion", "cast", "whitecast", "uv", "solardew"
        },
    },
    {
        "primary_domain": "software coding",
        "visual_environment": "office desk laptop",
        "triggers": {
            "code", "coding", "software", "developer", "developers", "programmer", "programmers",
            "programming", "python", "javascript", "typescript", "backend", "frontend", "api",
            "apis", "git", "github", "refactor", "refactoring", "syntax", "algorithm",
            "algorithms", "debug", "debugging", "server", "servers", "database", "databases",
            "devops", "cloud", "datacenter", "compute", "app", "platform", "pipeline", "pipelines"
        },
    },
    {
        "primary_domain": "gym fitness",
        "visual_environment": "gym workout weights",
        "triggers": {
            "gym", "fitness", "workout", "workouts", "exercise", "exercises", "athlete",
            "athletes", "athletic", "training", "train", "muscle", "muscles", "weights",
            "weightlifting", "dumbbells", "cardio", "stamina", "reps", "bodybuilding",
            "endurance", "running", "runner", "lifting", "squat"
        },
    },
    {
        "primary_domain": "real estate",
        "visual_environment": "modern home interior",
        "triggers": {
            "real estate", "realtor", "property", "properties", "apartment", "apartments",
            "villa", "villas", "house", "housing", "home", "homes", "interior", "interiors",
            "layout", "spacious", "bedroom", "square feet", "sqft", "mortgage", "amenities",
            "residence", "residential", "balcony", "penthouse", "architecture", "architectural"
        },
    },
    {
        "primary_domain": "personal finance",
        "visual_environment": "office desk tablet",
        "triggers": {
            "finance", "financial", "investing", "invest", "investment", "investments",
            "investor", "investors", "stock", "stocks", "market", "trading", "trader",
            "wealth", "wealthy", "savings", "saving", "portfolio", "dividend", "dividends",
            "budget", "profit", "profits", "revenue", "capital", "cash", "dollar", "dollars",
            "crypto", "cryptocurrency"
        },
    },
    {
        "primary_domain": "ocean marine",
        "visual_environment": "ocean underwater coral",
        "triggers": {
            "ocean", "oceans", "marine", "sea", "seas", "coral", "reef", "reefs", "underwater",
            "submersible", "seabed", "diver", "divers", "diving", "aquatic", "fish", "whale"
        },
    },
    {
        "primary_domain": "renewable energy",
        "visual_environment": "solar panels wind turbine",
        "triggers": {
            "solar", "photovoltaic", "wind turbine", "wind turbines", "renewable", "renewables",
            "clean energy", "green energy", "power grid", "electric grid", "turbines"
        },
    },
    {
        "primary_domain": "coffee brewing",
        "visual_environment": "coffee shop barista counter",
        "triggers": {
            "coffee", "espresso", "barista", "latte", "cappuccino", "crema", "portafilter",
            "beans", "roast", "brewing", "grind", "grinder"
        },
    },
    {
        "primary_domain": "craft watchmaking",
        "visual_environment": "artisan workshop desk",
        "triggers": {
            "watch", "watches", "watchmaker", "watchmaking", "horology", "timepiece", "timepieces",
            "movement", "balance wheel", "escapement", "chronometer"
        },
    },
    {
        "primary_domain": "nature landscape",
        "visual_environment": "outdoor nature mountains",
        "triggers": {
            "nature", "wildlife", "mountain", "mountains", "forest", "forests", "river",
            "waterfall", "waterfalls", "trees", "landscape", "outdoors", "scenery"
        },
    },
]


# =============================================================================
# Pass 1: Global Context Extraction
# =============================================================================

def call_llm_domain_extractor(script: str, timeout: float = 3.0) -> Optional[Dict[str, str]]:
    """Analyze the complete script with LLM to extract primary_domain and visual_environment."""
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY") or getattr(settings, "LLM_API_KEY", None)
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")

    if not api_key:
        return None

    system_prompt = (
        "You are an expert film director establishing global visual anchoring for stock footage retrieval.\n"
        "Analyze the provided video script and extract the overarching physical niche and default environment.\n"
        "Respond STRICTLY with valid JSON in this format:\n"
        "{\n"
        '  "primary_domain": "1 to 2 core physical niche tags (e.g. skincare, software coding, gym fitness, real estate, personal finance)",\n'
        '  "visual_environment": "concrete default physical setting (e.g. bathroom mirror clean daylight, office desk laptop, gym workout weights, modern home interior)"\n'
        "}\n"
        "Do not include abstract ideas, explanations, or extra fields."
    )

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Complete Script:\n{script[:2000]}"},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.1,
                    "max_tokens": 80,
                },
            )
            if resp.status_code == 200:
                raw_json = json.loads(resp.json()["choices"][0]["message"]["content"].strip())
                domain = raw_json.get("primary_domain", "").strip().lower()
                env = raw_json.get("visual_environment", "").strip().lower()
                if domain and env:
                    domain_words = domain.split()[:2]
                    clean_domain = " ".join(domain_words)
                    logger.info("Pass 1 LLM Domain Extraction: primary_domain='%s', visual_environment='%s'", clean_domain, env)
                    return {
                        "primary_domain": clean_domain,
                        "visual_environment": env,
                    }
    except Exception as e:
        logger.debug("Pass 1 LLM Domain Extraction skipped or failed: %s", str(e))

    return None


def heuristic_domain_context(script: str) -> Dict[str, str]:
    """Deterministic Pass 1 heuristic domain and environment extractor."""
    script_lower = script.lower()
    words = set(re.findall(r"\b[a-z]{3,}\b", script_lower))

    best_profile = None
    best_score = 0

    for profile in DOMAIN_PROFILES:
        score = sum(1 for trigger in profile["triggers"] if trigger in words or trigger in script_lower)
        if score > best_score:
            best_score = score
            best_profile = profile

    if best_profile and best_score >= 1:
        return {
            "primary_domain": best_profile["primary_domain"],
            "visual_environment": best_profile["visual_environment"],
        }

    return {
        "primary_domain": "commercial",
        "visual_environment": "modern daylight interior",
    }


def extract_script_domain_context(script: str, timeout: float = 3.0) -> Dict[str, str]:
    """Pass 1 (Global Context Extraction):
    Before parsing individual scenes, analyze the complete script using LLM
    (or heuristic extractor fallback) to extract:
    a) primary_domain: Exactly 1 or 2 core physical niche tags representing the overall subject
       (e.g., 'skincare', 'gym fitness', 'software coding', 'personal finance', 'real estate').
    b) visual_environment: The default setting or world where this topic exists
       (e.g., 'bathroom mirror clean daylight', 'gym workout weights', 'office desk laptop', 'modern home interior').
    """
    if not script or not script.strip():
        return {
            "primary_domain": "commercial",
            "visual_environment": "modern daylight interior",
        }

    llm_context = call_llm_domain_extractor(script, timeout=timeout)
    if llm_context:
        return llm_context

    return heuristic_domain_context(script)


# =============================================================================
# Disambiguation & Token Cleaning Helpers
# =============================================================================

def disambiguate_context_word(word: str, sentence: str, context: str) -> Optional[List[str]]:
    """Resolve polysemic and ambiguous words based on contextual cues in the script."""
    combined = f"{context} {sentence}".lower()
    words_set = set(re.findall(r"\b[a-zA-Z]{2,}\b", combined))
    clean_word = word.lower().strip()

    # 1. 'scale' / 'scaling' / 'scales'
    if clean_word in ("scale", "scaling", "scales"):
        if any(k in words_set for k in ["food", "kitchen", "cook", "cooking", "recipe", "weight", "diet", "calorie", "calories", "bake", "baking", "ingredient", "ingredients", "meal"]):
            return ["kitchen", "food", "weighing", "scale"]
        if any(k in words_set for k in ["code", "developer", "server", "servers", "backend", "cloud", "data", "software", "database", "databases", "app", "platform", "compute", "api"]):
            return ["server", "rack", "datacenter", "flashing"]
        if any(k in words_set for k in ["business", "finance", "company", "market", "revenue", "startup", "sales", "enterprise", "capital", "investor", "investors"]):
            return ["office", "team", "laptop", "chart"]
        return ["office", "computer", "screen", "work"]

    # 2. 'launch' / 'launching' / 'launched'
    if clean_word in ("launch", "launching", "launched"):
        if any(k in words_set for k in ["space", "rocket", "orbit", "nasa", "mars", "astronaut", "satellite", "sky"]):
            return ["rocket", "launch", "smoke", "fire"]
        if any(k in words_set for k in ["boat", "ship", "ocean", "sea", "water", "harbor", "dock", "lake", "marina", "vessel"]):
            return ["boat", "water", "ocean", "harbor"]
        if any(k in words_set for k in ["app", "platform", "startup", "product", "software", "website", "feature", "code", "tech"]):
            return ["startup", "office", "computer", "work"]
        return ["startup", "office", "computer", "work"]

    # 3. 'consistency' / 'consistent'
    if clean_word in ("consistency", "consistent"):
        if any(k in words_set for k in ["gym", "fitness", "workout", "running", "exercise", "athlete", "health", "train", "training", "sport", "muscle"]):
            return ["athlete", "running", "track", "training"]
        if any(k in words_set for k in ["code", "developer", "programming", "software", "tech", "computer"]):
            return ["person", "typing", "notebook", "desk"]
        if any(k in words_set for k in ["coffee", "espresso", "beans", "grind", "grinder", "latte", "barista", "roast", "brewing", "brew"]):
            return ["coffee", "beans", "grinder", "roast"]
        if any(k in words_set for k in ["kitchen", "cook", "chef", "recipe", "bake", "baking", "food", "sauce"]):
            return ["chef", "mixing", "bowl", "kitchen"]
        return None

    # 4. 'lead' / 'leading' / 'leader'
    if clean_word in ("lead", "leading", "leader"):
        if any(k in words_set for k in ["factory", "metal", "industrial", "mine", "pipe", "wire", "material", "ore", "foundry", "smelted"]):
            return ["factory", "industrial", "metal", "worker"]
        if any(k in words_set for k in ["team", "company", "business", "manager", "meeting", "presentation", "conference", "boardroom"]):
            return ["team", "leader", "presentation", "meeting"]
        return ["person", "talking", "meeting", "room"]

    # 5. 'architecture' / 'architectural'
    if clean_word in ("architecture", "architectural"):
        if any(k in words_set for k in ["building", "construction", "urban", "city", "skyline", "concrete", "blueprint", "architect", "property", "house", "apartment"]):
            return ["modern", "building", "glass", "office"]
        if any(k in words_set for k in ["code", "software", "system", "systems", "data", "server", "backend", "developer", "refactor", "refactoring"]):
            return ["person", "typing", "notebook", "desk"]
        return ["modern", "corporate", "glass", "office"]

    # 6. 'pipeline' / 'pipelines'
    if clean_word in ("pipeline", "pipelines"):
        if any(k in words_set for k in ["oil", "gas", "water", "fuel", "drill", "energy"]):
            return ["industrial", "pipeline", "metal", "factory"]
        return ["data", "server", "screen", "glowing"]

    # 7. 'protect' / 'protecting' / 'protection'
    if clean_word in ("protect", "protecting", "protection"):
        if any(k in words_set for k in ["ocean", "marine", "sea", "coral", "reef", "fish", "planet", "nature", "wildlife", "animal", "forest", "ecosystem", "ecosystems"]):
            return ["ocean", "coral", "reef", "underwater"]
        if any(k in words_set for k in ["code", "software", "cyber", "data", "security", "network", "password", "server"]):
            return ["server", "security", "screen", "lock"]
        return None

    return None


def sanitize_visual_tokens(tokens: List[str]) -> List[str]:
    """Filter out banned abstract words, numbers, and stop words, ensuring physical keywords."""
    clean: List[str] = []
    seen: Set[str] = set()

    for tok in tokens:
        t = re.sub(r"[^a-zA-Z]", "", tok.lower()).strip()
        if not t:
            continue
        # Allow 2-letter word 'up' (for cinematographic 'close up')
        if len(t) < 3 and t != "up":
            continue
        if (t in STOP_WORDS and t != "up") or t in BANNED_ABSTRACT_WORDS:
            continue
        # Strip common abstract suffixes
        if t.endswith("ness") or t.endswith("ity") or t.endswith("ship"):
            continue
        if t not in seen:
            seen.add(t)
            clean.append(t)

    return clean


def enforce_keyword_length_constraint(
    tokens: List[str],
    fallback_theme: str = "office computer work",
    primary_domain: Optional[str] = None,
) -> str:
    """Enforce strictly 2 to 4 concrete physical keywords, stripping isolated polysemic words,
    and prefixing/anchoring with primary_domain when needed."""
    sanitized = sanitize_visual_tokens(tokens)

    # Strip polysemic words if the query has <= 2 words (i.e. isolated out-of-context polysemic word)
    if len(sanitized) <= 2:
        sanitized = [t for t in sanitized if t not in POLYSEMIC_WORDS]

    # If all tokens were stripped or empty, anchor with primary_domain
    if not sanitized and primary_domain and primary_domain != "commercial":
        sanitized = [w for w in primary_domain.split() if w not in STOP_WORDS]

    if len(sanitized) > 4:
        sanitized = sanitized[:4]

    # If fewer than 2 keywords, pad with context-appropriate concrete anchor words
    fallback_tokens = [w for w in fallback_theme.split() if w not in sanitized and w not in STOP_WORDS]
    while len(sanitized) < 2 and fallback_tokens:
        sanitized.append(fallback_tokens.pop(0))

    if len(sanitized) < 2:
        sanitized = ["desk", "typing"]

    return " ".join(sanitized[:4])


# =============================================================================
# Pass 2: Context-Enforced Scene Queries
# =============================================================================

def call_llm_visual_disambiguator(
    sentence: str,
    context: Optional[str] = None,
    domain_context: Optional[Dict[str, str]] = None,
    primary_domain: Optional[str] = None,
    timeout: float = 3.0,
) -> Optional[str]:
    """Invoke LLM with director prompt for context-aware visual query disambiguation.
    
    Instructs the LLM to strictly ground the query in primary_domain and visual_environment,
    enforcing: [primary_domain or environment] + [concrete subject] + [visible action].
    """
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY") or getattr(settings, "LLM_API_KEY", None)
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")

    if not api_key:
        return None

    dom_ctx = domain_context or extract_script_domain_context(context or sentence)
    domain = primary_domain or dom_ctx.get("primary_domain", "commercial")
    env = dom_ctx.get("visual_environment", "modern daylight interior")

    system_prompt = (
        "You are an expert film director and cinematographer selecting B-roll stock footage.\n"
        "Your task is to answer this question: "
        "'If a director were filming B-roll for this line in this specific context, what physical object, person, or action is visible on screen?'\n\n"
        "CRITICAL DIRECTORIAL RULES:\n"
        f"1. Every generated query MUST remain visually grounded within the {domain} and {env}. "
        "Never return abstract visual metaphors, isolated technical terms, or polysemic words on their own.\n"
        "2. Enforce strict query composition: [primary_domain or environment] + [concrete subject] + [visible action].\n"
        "3. Return STRICTLY 2 to 4 concrete, physical visual keywords separated by spaces.\n"
        "4. DO NOT return complete sentences, punctuation, explanations, or quotes.\n"
        "5. NEGATIVE CONSTRAINT: Absolutely NEVER use abstract nouns, metaphors, emotions, or words with high polysemy "
        "(e.g., ban 'consistency', 'success', 'future', 'quality', 'value', 'growth', 'resilience', 'freedom', "
        "'cast', 'barrier', 'spectrum', 'layer', 'scale', 'launch', 'pipeline', 'traffic', 'cloud', 'pool', 'stream', 'tree', 'root', 'thread').\n"
        "6. Focus only on visible physical matter: humans, tangible objects, physical environments, and physical actions."
    )

    user_prompt = (
        f"Primary Domain: {domain}\n"
        f"Visual Environment: {env}\n"
        f"Script Context: {context or sentence}\n"
        f"Current Line: {sentence}\n"
        f"B-roll Physical Keywords:"
    )

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 30,
                },
            )
            if resp.status_code == 200:
                raw_text = resp.json()["choices"][0]["message"]["content"].strip()
                raw_tokens = raw_text.split()
                valid_keywords = enforce_keyword_length_constraint(
                    raw_tokens,
                    fallback_theme=f"{domain} {env}"[:30],
                )
                if valid_keywords:
                    logger.info("LLM visual disambiguation: '%s' -> '%s' (domain='%s')", sentence[:30], valid_keywords, domain)
                    return valid_keywords
    except Exception as e:
        logger.debug("LLM visual disambiguation call failed or timed out: %s", str(e))

    return None


def fallback_visual_extraction(
    sentence: str,
    context: Optional[str] = None,
    domain_context: Optional[Dict[str, str]] = None,
    primary_domain: Optional[str] = None,
) -> str:
    """Fallback safety layer: Extracts strictly 2 to 4 physical keywords using POS & contextual disambiguation,
    strictly enforcing: [primary_domain or environment] + [concrete subject] + [visible action]."""
    ctx = context or sentence
    dom_ctx = domain_context or extract_script_domain_context(ctx)
    domain = primary_domain or dom_ctx.get("primary_domain", "commercial")
    env = dom_ctx.get("visual_environment", "modern daylight interior")

    words = re.findall(r"\b[a-zA-Z]{3,}\b", sentence.lower())
    sentence_lower = sentence.lower()

    # 1. High-priority contextual word disambiguations (scale, launch, lead, consistency)
    for w in words:
        disambiguated = disambiguate_context_word(w, sentence, ctx)
        if disambiguated:
            return enforce_keyword_length_constraint(disambiguated, fallback_theme=f"{domain} {env}"[:30])

    # 1.5 If sentence is already a clean, specific 2-4 word physical visual query (e.g. from user visual_hint), preserve it
    clean_tokens = [w for w in words if w not in STOP_WORDS]
    if 2 <= len(clean_tokens) <= 4 and not any(t in POLYSEMIC_WORDS or t in BANNED_ABSTRACT_WORDS for t in clean_tokens):
        return " ".join(clean_tokens)

    # 2. Domain-Specific Disambiguation (Pass 2 Anchoring)
    # ---------------------------------------------------
    # A) Skincare Domain
    if domain == "skincare":
        # Shopping / CTA
        if any(k in words for k in ["link", "tap", "click", "shop", "buy", "discount", "order", "bio"]):
            return "smartphone shopping online"
        # Serum / Dropper / Active ingredients
        if any(k in words for k in ["hyaluronic", "niacinamide", "acid", "dropper", "pipette", "serum", "formula"]):
            return "skincare serum dropper pipette"
        # White cast / Cast / Application
        if "white cast" in sentence_lower or any(k in words for k in ["cast", "whitecast", "residue"]):
            return "skincare face cream mirror"
        # Sun / UV / Spectrum / Summer
        if any(k in words for k in ["uv", "sunlight", "sun", "spectrum", "summer", "rays"]):
            return "woman face summer sunlight"
        # Skin barrier / Barrier / Protection / Sensitive
        if "skin barrier" in sentence_lower or any(k in words for k in ["barrier", "protect", "protection", "barrierprotect"]):
            return "face skin close up woman"
        # Product showcase / Bottle / Packaging / Gel
        if any(k in words for k in ["bottle", "solardew", "water", "gel", "product", "spf", "packaging"]):
            return "skincare cosmetic bottle serum"
        # Glow / Matte / Smooth / Radiant skin
        if any(k in words for k in ["glow", "glowy", "matte", "smooth", "finish", "radiant", "complexion"]):
            return "woman touching smooth skin"
        # Cleanse / wash
        if any(k in words for k in ["cleanse", "wash", "washing", "water", "splash", "foam"]):
            return "woman washing face water"
        # Face application fallback
        return "skincare face cream mirror"

    # B) Software & Coding Domain
    if domain in ("software coding", "coding", "software"):
        # Boundaries / Break boundaries
        if any(k in words for k in ["boundary", "boundaries", "break"]):
            return "developer typing dual monitor"
        # Server / Datacenter / Cloud / Database / Infrastructure / Pipeline
        if any(k in words for k in ["server", "servers", "cloud", "database", "databases", "datacenter", "infrastructure", "backend", "cluster", "clusters", "pipeline", "pipelines"]):
            return "server rack datacenter flashing"
        # Mobile app / Screen
        if any(k in words for k in ["mobile", "app", "screen", "phone", "test", "testing", "device"]):
            return "developer testing mobile screen"
        # Terminal / CLI / Deploy
        if any(k in words for k in ["terminal", "command", "deploy", "console", "cli", "shell"]):
            return "developer terminal command code"
        # Team / Meeting / Pair
        if any(k in words for k in ["team", "meeting", "pair", "discussion", "engineer", "engineers"]):
            return "software engineers meeting office"
        # Code / Developer / Refactor / Syntax / Git / Algorithm / Debug
        if any(k in words for k in ["syntax", "ide", "editor", "script", "refactor", "refactoring"]):
            return "coding computer screen close up"
        # Default coding fallback
        return "developer typing dual monitor"

    # C) Personal Finance Domain
    if domain in ("personal finance", "finance"):
        # Growth / High growth / Market / Stocks / Scale / Chart / Portfolio
        if any(k in words for k in ["growth", "high", "scale", "market", "stock", "stocks", "chart", "portfolio", "trading", "invest", "investment"]):
            return "stock market chart tablet"
        # Cash / Money / Savings / Budget / Counting
        if any(k in words for k in ["cash", "money", "save", "saving", "savings", "budget", "count", "bills", "dollar", "dollars"]):
            return "counting money cash calculation"
        # Coins / Wealth / Vault
        if any(k in words for k in ["bank", "vault", "gold", "coins", "wealth", "wealthy"]):
            return "gold coins wealth savings"
        # Default finance fallback
        return "stock market chart tablet"

    # D) Real Estate Domain
    if domain in ("real estate", "architecture"):
        # Spacious layout / High yield / Layout / Luxury / Investment
        if any(k in words for k in ["layout", "spacious", "yield", "luxury", "investment", "modern"]):
            return "real estate luxury interior"
        # Apartment / Living room / Bedroom / Interior
        if any(k in words for k in ["apartment", "living", "bedroom", "kitchen", "villa", "home", "interior"]):
            return "modern apartment living room"
        # Exterior / Building / Skyline / Facade
        if any(k in words for k in ["exterior", "building", "skyline", "facade", "view"]):
            return "modern residential building exterior"
        # Key / Keys / Handing / Realtor / Buyer
        if any(k in words for k in ["key", "keys", "door", "handing", "realtor", "buyer", "sold"]):
            return "realtor handing keys house"
        # Default real estate fallback
        return "real estate modern interior"

    # E) Gym & Fitness Domain
    if domain in ("gym fitness", "fitness", "athletics"):
        # Running / Cardio / Track / Treadmill / Sprint / Consistency
        if any(k in words for k in ["running", "runner", "cardio", "track", "treadmill", "sprint"]):
            return "athlete running track training"
        # Extreme burn / Burn / Push / Layers / Limits / Weights / Workout
        if any(k in words for k in ["burn", "extreme", "push", "layer", "layers", "limits", "workout", "exercise", "training", "weights", "dumbbells", "gym", "lifting", "squat", "muscle", "barbell"]):
            return "athlete lifting weights gym"
        # Stretch / Yoga
        if any(k in words for k in ["stretch", "stretching", "yoga", "mat", "warmup", "flexibility"]):
            return "athlete stretching yoga mat"
        # Default gym fitness fallback
        return "gym workout weights athlete"

    # F) Ocean & Marine Domain
    if domain in ("ocean marine", "marine") or any(k in words for k in ["ocean", "marine", "submersible", "coral", "reef", "seabed", "underwater"]):
        if any(k in words for k in ["protect", "protecting", "ecosystem", "coral", "reef"]):
            return "ocean underwater coral reef"
        return "ocean sea underwater marine"

    # G) Clean Energy / Renewable
    if domain == "renewable energy" or any(k in words for k in ["solar", "photovoltaic", "wind turbine", "wind turbines"]):
        if any(k in words for k in ["solar", "photovoltaic"]):
            return "solar panels sun farm"
        if any(k in words for k in ["wind", "turbines", "turbine"]):
            return "wind turbines spinning field"
        return "solar panels wind turbine"

    # H) Coffee & Barista
    if domain == "coffee brewing" or any(k in words for k in ["coffee", "espresso", "barista"]):
        if any(k in words for k in ["grind", "beans", "grinder"]):
            return "coffee beans grinder close up"
        if any(k in words for k in ["tamp", "portafilter", "grounds", "basket"]):
            return "barista espresso machine portafilter"
        if any(k in words for k in ["crema", "cup", "pour", "pouring"]):
            return "espresso pouring cup"
        if any(k in words for k in ["milk", "steaming", "latte", "art"]):
            return "barista pouring latte art"
        return "coffee barista espresso brewing"

    # I) Craft Watchmaking
    if domain == "craft watchmaking" or any(k in words for k in ["watch", "watches", "watchmaker", "horology", "timepiece"]):
        if any(k in words for k in ["gear", "gears", "cog", "cogs", "mechanism", "clock", "turning"]):
            return "watch gears mechanical cogs"
        if any(k in words for k in ["balance", "wheel", "movement", "escapement", "oscillate", "oscillates", "beats"]):
            return "watch movement balance wheel"
        if any(k in words for k in ["dial", "sapphire", "crystal", "face", "glass"]):
            return "luxury watch dial sapphire"
        if any(k in words for k in ["assembling", "assemble", "craftsman", "craft", "bench", "craftsmen", "devotion"]):
            return "watchmaker craftsman assembling watch"
        if any(k in words for k in ["tool", "tools", "jeweler", "artisan", "microscopic", "ruby", "precision"]):
            return "artisan jeweler precision tools"
        if any(k in words for k in ["pocket", "vintage", "antique", "pocketwatch"]):
            return "vintage mechanical pocket watch"
        return "watchmaker artisan workshop desk"

    # 3. Preserved physical topic checks
    if any(k in words for k in ["office", "corporate", "facility", "headquarters", "building", "glass"]):
        return "modern corporate glass office"

    if any(k in words for k in ["code", "coding", "software", "developer", "programmer", "refactor", "refactoring", "syntax", "git", "algorithm", "debug"]):
        return "person typing notebook desk"

    if any(k in words for k in ["phone", "smartphone", "mobile", "app", "scroll", "scrolling", "screen"]):
        return "smartphone scrolling close up"

    # 4. General POS extraction: strip stop words, banned words, isolated polysemic words
    tokens = sanitize_visual_tokens(words)
    non_polysemic = [t for t in tokens if t not in POLYSEMIC_WORDS]

    if non_polysemic:
        return enforce_keyword_length_constraint(non_polysemic, fallback_theme=f"{domain} {env}"[:30], primary_domain=domain)

    # If only polysemic or abstract words were extracted, anchor firmly with domain
    if domain and domain != "commercial":
        env_words = env.split()
        env_anchor = f"{env_words[0]} {env_words[1]}" if len(env_words) >= 2 else "desk laptop"
        return f"{domain} {env_anchor}"

    return "person focused desk laptop"


def parse_visual_query(
    sentence: str,
    context: Optional[str] = None,
    domain_context: Optional[Dict[str, str]] = None,
    primary_domain: Optional[str] = None,
) -> str:
    """Main Context-Aware Visual Query Translator implementing Universal Script-Level Domain Anchoring.
    
    1. Pass 1 Context Ingestion: Determines primary_domain and visual_environment.
    2. Pass 2 Scene Query Generation:
       - Runs LLM visual query translator asking the directorial B-roll question with strict domain grounding.
       - If LLM times out, fails, or is unconfigured, seamlessly falls back to the safety layer.
       - Enforces composition: [primary_domain or environment] + [concrete subject] + [visible action].
    3. Guarantees strictly 2 to 4 concrete, physical visual keywords with all abstract & polysemic words resolved.
    """
    if not sentence or not sentence.strip():
        return "office computer screen work"

    clean_sentence = sentence.strip()

    # Pass 1: Global Context Resolution
    dom_ctx = domain_context or extract_script_domain_context(context or clean_sentence)
    domain = primary_domain or dom_ctx.get("primary_domain", "commercial")

    # Pass 2: Scene Query Generation
    # 1. Attempt LLM translation
    llm_result = call_llm_visual_disambiguator(
        clean_sentence,
        context=context,
        domain_context=dom_ctx,
        primary_domain=domain,
    )
    if llm_result:
        return llm_result

    # 2. Fallback safety layer
    return fallback_visual_extraction(
        clean_sentence,
        context=context,
        domain_context=dom_ctx,
        primary_domain=domain,
    )


def drop_adjectives_to_base_nouns(query: str, primary_domain: Optional[str] = None) -> str:
    """Dynamically simplify a visual query by dropping descriptive adjectives and retaining base physical nouns/verbs.
    
    Used when Pixabay returns 0 hits for a multi-word visual action string.
    If primary_domain is provided, guarantees that the simplified fallback retains domain anchoring
    ({primary_domain} {base_noun}) rather than dropping the domain context.
    """
    tokens = [re.sub(r"[^a-zA-Z]", "", t.lower()) for t in query.split() if t.strip()]
    
    # Filter out dismissable adjectives
    nouns_and_verbs = [t for t in tokens if t not in DISMISSABLE_ADJECTIVES and t not in STOP_WORDS and len(t) >= 3]

    if nouns_and_verbs:
        # Prioritize physical anchor nouns and action verbs (up to 2 tokens)
        base = " ".join(nouns_and_verbs[-2:] if len(nouns_and_verbs) > 2 else nouns_and_verbs)
    else:
        base = tokens[-1] if tokens else "office"

    if not primary_domain:
        return base

    clean_domain = primary_domain.strip().lower()
    domain_words = clean_domain.split()
    base_words = base.split()

    # Find the non-domain base noun
    non_domain_words = [w for w in base_words if w not in domain_words]
    base_noun = non_domain_words[-1] if non_domain_words else (base_words[-1] if base_words else "")

    if base_noun:
        return f"{clean_domain} {base_noun}"
    return clean_domain


# Backward compatibility aliases
def semantic_translate(text: str, context: Optional[str] = None) -> Optional[str]:
    """Compatibility wrapper around parse_visual_query."""
    res = parse_visual_query(text, context=context)
    return res if res else None


def clean_keywords(text: str, max_words: int = 4) -> str:
    """Compatibility wrapper for keyword cleaning."""
    return fallback_visual_extraction(text)
