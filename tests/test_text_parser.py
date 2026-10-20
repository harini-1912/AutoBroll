"""Unit tests for Context-Aware Visual Disambiguation and Pixabay dynamic retry."""
import pytest
from unittest.mock import MagicMock, patch

from app.services.text_parser import (
    parse_visual_query,
    disambiguate_context_word,
    fallback_visual_extraction,
    call_llm_visual_disambiguator,
    drop_adjectives_to_base_nouns,
    extract_script_domain_context,
    BANNED_ABSTRACT_WORDS,
    POLYSEMIC_WORDS,
)
from app.providers.pixabay import PixabayFootageProvider


def test_context_aware_disambiguation_scale():
    """Verify 'scale' resolves differently depending on script context."""
    # 1. Cooking / diet context -> kitchen food weighing scale
    res_cooking = parse_visual_query(
        "We need to carefully check the scale of our ingredients.",
        context="In this healthy meal prep recipe, cooking in the kitchen requires exact nutrition and calories."
    )
    assert res_cooking == "kitchen food weighing scale"
    assert 2 <= len(res_cooking.split()) <= 4

    # 2. Tech / server context -> server rack datacenter flashing
    res_tech = parse_visual_query(
        "Our backend must scale smoothly across all regions.",
        context="Building scalable software on cloud infrastructure with high throughput database servers."
    )
    assert res_tech == "server rack datacenter flashing"
    assert 2 <= len(res_tech.split()) <= 4

    # 3. Business / finance context -> office team laptop chart
    res_biz = parse_visual_query(
        "The startup will scale revenue rapidly next year.",
        context="Our enterprise business strategy focuses on market sales growth and investor capital."
    )
    assert res_biz == "office team laptop chart"


def test_context_aware_disambiguation_launch():
    """Verify 'launch' resolves differently based on context."""
    # 1. Space context -> rocket launch smoke fire
    res_space = parse_visual_query(
        "The countdown reached zero before the launch.",
        context="NASA astronauts prepared the rocket spacecraft for orbit into the dark sky."
    )
    assert res_space == "rocket launch smoke fire"

    # 2. Tech startup context -> startup office computer work
    res_startup = parse_visual_query(
        "We are preparing for our big product launch.",
        context="The developer team finalized the web app platform for user onboarding."
    )
    assert res_startup == "startup office computer work"

    # 3. Boat context -> boat water ocean harbor
    res_boat = parse_visual_query(
        "We watched the launch of the vessel into the bay.",
        context="At the marina dock, the sailing boat entered the deep ocean water."
    )
    assert res_boat == "boat water ocean harbor"


def test_context_aware_disambiguation_consistency_and_lead():
    """Verify 'consistency' and 'lead' resolve to physical visual actions without abstract words."""
    # 'consistency' in fitness context
    res_fitness = parse_visual_query(
        "Consistency is the secret to peak physical transformation.",
        context="Daily workout routines in the gym build athletic endurance and muscle fitness."
    )
    assert res_fitness == "athlete running track training"
    # Ensure abstract words 'consistency' and 'transformation' are strictly absent
    for banned in ["consistency", "transformation", "secret", "physical"]:
        assert banned not in res_fitness.split()

    # 'lead' in business context
    res_lead_biz = parse_visual_query(
        "She was chosen to lead the initiative forward.",
        context="In the executive boardroom, the company business team discussed quarterly milestones."
    )
    assert res_lead_biz == "team leader presentation meeting"

    # 'lead' in industrial metal context
    res_lead_metal = parse_visual_query(
        "Workers smelted lead and raw ore in the foundry.",
        context="Heavy industry factory facility processing industrial metal pipes and minerals."
    )
    assert res_lead_metal == "factory industrial metal worker"


def test_strict_negative_constraint_and_length_constraint():
    """Verify that abstract nouns, metaphors, and emotions are banned and output is 2 to 4 keywords."""
    abstract_sentence = "Unlocking true freedom, consistency, and future quality leads to immense success and resilience."
    res = parse_visual_query(abstract_sentence, context="A developer writing clean code at a desk.")
    tokens = res.split()

    # Constraint: Strictly 2 to 4 words
    assert 2 <= len(tokens) <= 4

    # Negative constraint: Banned abstract words must NOT appear in output
    for tok in tokens:
        assert tok not in BANNED_ABSTRACT_WORDS


def test_llm_visual_disambiguator_success_and_banned_word_filtering():
    """Verify LLM visual translator output parsing and banned words stripping."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": "smartphone scrolling close up"
                }
            }
        ]
    }

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test_openai_key"}):
        with patch("httpx.Client.post", return_value=mock_resp):
            res = parse_visual_query("Check out the new application feature.")
            assert res == "smartphone scrolling close up"
            assert 2 <= len(res.split()) <= 4


def test_llm_timeout_triggers_fallback_safety_layer():
    """Verify that LLM timeout or connection failure triggers the fallback safety layer without error."""
    import httpx

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test_openai_key"}):
        with patch("httpx.Client.post", side_effect=httpx.TimeoutException("LLM request timed out")):
            # Fallback layer handles it gracefully
            res = parse_visual_query("Clean code requires continuous care and disciplined execution.")
            assert res == "developer typing dual monitor"
            assert 2 <= len(res.split()) <= 4


def test_drop_adjectives_to_base_nouns():
    """Verify helper drops descriptive adjectives to retain base physical nouns/verbs."""
    assert drop_adjectives_to_base_nouns("modern corporate glass office") == "office"
    assert drop_adjectives_to_base_nouns("person typing notebook desk") == "notebook desk"
    assert drop_adjectives_to_base_nouns("smartphone scrolling close up") == "smartphone scrolling"


def test_pixabay_dynamic_retry_on_zero_hits():
    """Verify PixabayFootageProvider dynamically retries by dropping adjectives on 0 hits."""
    provider = PixabayFootageProvider(api_key="secret_test_key_pixabay")

    # First call for "modern corporate glass office" returns 0 hits
    mock_resp_empty = MagicMock()
    mock_resp_empty.status_code = 200
    mock_resp_empty.json.return_value = {"total": 0, "hits": []}

    # Second retry call for "office" returns valid hit
    mock_resp_retry = MagicMock()
    mock_resp_retry.status_code = 200
    mock_resp_retry.json.return_value = {
        "total": 1,
        "hits": [
            {
                "id": 9988,
                "duration": 10,
                "tags": "office, work, desk",
                "videos": {
                    "large": {
                        "url": "https://pixabay.test/office_clip.mp4",
                        "width": 1920,
                        "height": 1080,
                        "size": 12000000,
                    }
                },
                "user": "StudioPro",
            }
        ],
    }

    with patch("httpx.Client.get", side_effect=[mock_resp_empty, mock_resp_retry]) as mock_get:
        candidates = provider.search("An elite modern corporate glass office facility.")

        assert len(candidates) == 1
        assert candidates[0].clip_id == "pixabay_9988"
        assert candidates[0].download_url == "https://pixabay.test/office_clip.mp4"

        # Verify that get was called twice: once with visual query, then dynamically with simplified base nouns
        assert mock_get.call_count == 2
        first_call_params = mock_get.call_args_list[0][1]["params"]
        second_call_params = mock_get.call_args_list[1][1]["params"]

        assert "modern" in first_call_params["q"] or "office" in first_call_params["q"]
        assert second_call_params["q"] == "office"


# =============================================================================
# Universal Script-Level Domain Anchoring Tests
# =============================================================================

def test_two_pass_global_context_extraction():
    """Verify Pass 1 extracts primary_domain and visual_environment across distinct domains."""
    # 1. Skincare script
    skincare_script = "Daily sunscreen application protects the skin barrier and prevents sun damage without a greasy white cast."
    ctx_skin = extract_script_domain_context(skincare_script)
    assert ctx_skin["primary_domain"] == "skincare"
    assert "bathroom" in ctx_skin["visual_environment"] or "mirror" in ctx_skin["visual_environment"]

    # 2. Software / Coding script
    coding_script = "Our developer team writes clean python code, refactoring git repos and deploying scalable database microservices."
    ctx_code = extract_script_domain_context(coding_script)
    assert ctx_code["primary_domain"] == "software coding"
    assert "office" in ctx_code["visual_environment"] or "laptop" in ctx_code["visual_environment"]

    # 3. Real Estate script
    re_script = "This luxury villa features a spacious layout with four bedroom suites and high rental yield for investors."
    ctx_re = extract_script_domain_context(re_script)
    assert ctx_re["primary_domain"] == "real estate"
    assert "home" in ctx_re["visual_environment"] or "interior" in ctx_re["visual_environment"]

    # 4. Gym / Fitness script
    fitness_script = "Athletes hit the gym daily for intensive dumbbell weightlifting workouts to build muscular endurance."
    ctx_fit = extract_script_domain_context(fitness_script)
    assert ctx_fit["primary_domain"] == "gym fitness"
    assert "gym" in ctx_fit["visual_environment"] or "weights" in ctx_fit["visual_environment"]

    # 5. Personal Finance script
    finance_script = "Smart investing in the stock market and managing your budget portfolio yields steady dividend revenue."
    ctx_fin = extract_script_domain_context(finance_script)
    assert ctx_fin["primary_domain"] == "personal finance"


def test_context_enforced_scene_queries_and_polysemy_anchoring():
    """Verify Pass 2 context-enforced scene queries prevent out-of-context downloads."""
    # Skincare: 'white cast' -> skincare face cream mirror
    res_cast = parse_visual_query(
        "Never worry about an annoying white cast.",
        context="Daily sunscreen application protects the skin barrier without a heavy white cast."
    )
    assert res_cast == "skincare face cream mirror"
    assert "cast" not in res_cast.split()

    # Skincare: 'skin barrier' -> face skin close up woman
    res_barrier = parse_visual_query(
        "Reinforce your skin barrier against daily pollutants.",
        context="Our moisturizing skincare cream locks in hydration and fortifies the delicate skin barrier."
    )
    assert res_barrier == "face skin close up woman"
    assert "barrier" not in res_barrier.split()

    # Skincare: 'uv spectrum' -> woman face summer sunlight
    res_spectrum = parse_visual_query(
        "Blocks the full UV spectrum with broad protection.",
        context="Advanced SPF skincare formula defending against harsh summer UV rays."
    )
    assert res_spectrum == "woman face summer sunlight"
    assert "spectrum" not in res_spectrum.split()

    # Coding: 'break boundaries' -> developer typing dual monitor
    res_coding = parse_visual_query(
        "Break boundaries and deliver exceptional speed.",
        context="Our engineering team builds software code and backend APIs with high developer efficiency."
    )
    assert res_coding == "developer typing dual monitor"
    assert "boundaries" not in res_coding.split()

    # Coding: 'scale pipeline' -> server rack datacenter flashing
    res_pipeline = parse_visual_query(
        "Scale your deployment pipeline across global clusters.",
        context="Modern cloud software infrastructure with automated developer git pipelines and server monitoring."
    )
    assert res_pipeline == "server rack datacenter flashing"
    assert "pipeline" not in res_pipeline.split()
    assert "scale" not in res_pipeline.split()

    # Finance: 'high growth' -> stock market chart tablet
    res_finance = parse_visual_query(
        "Unlock high growth and compound your wealth.",
        context="Personal finance strategies, stock trading portfolios, and smart budgeting for investors."
    )
    assert res_finance == "stock market chart tablet"
    assert "growth" not in res_finance.split()

    # Real Estate: 'spacious layout' -> real estate luxury interior
    res_re = parse_visual_query(
        "Enjoy a spacious layout with sunlit panoramic views.",
        context="Exclusive real estate residential properties, luxury apartments, and modern interior architecture."
    )
    assert res_re == "real estate luxury interior"

    # Gym / Fitness: 'extreme burn' -> athlete lifting weights gym
    res_gym = parse_visual_query(
        "Feel the extreme burn as you push through limits.",
        context="High intensity gym fitness workout routine, weight training, and athletic muscle building."
    )
    assert res_gym == "athlete lifting weights gym"
    assert "burn" not in res_gym.split()


def test_polysemy_blacklist_membership():
    """Verify that high-risk polysemic words are in POLYSEMIC_WORDS."""
    required_words = ["cast", "barrier", "spectrum", "layer", "scale", "launch", "pipeline", "traffic", "funnel", "cloud", "pool", "stream", "tree", "root", "thread"]
    for poly in required_words:
        assert poly in POLYSEMIC_WORDS


def test_drop_adjectives_with_primary_domain():
    """Verify drop_adjectives_to_base_nouns retains {primary_domain} {base_noun}."""
    # When primary_domain is provided, it must not drop domain context
    assert drop_adjectives_to_base_nouns("modern luxury glass apartment", primary_domain="real estate") == "real estate apartment"
    assert drop_adjectives_to_base_nouns("skincare face cream mirror", primary_domain="skincare") == "skincare mirror"
    assert drop_adjectives_to_base_nouns("clean digital corporate office", primary_domain="software coding") == "software coding office"


def test_pixabay_retry_with_domain_anchoring():
    """Verify Pixabay retry retains primary_domain context when 0 hits occur."""
    provider = PixabayFootageProvider(api_key="secret_test_key_pixabay")

    mock_resp_empty = MagicMock()
    mock_resp_empty.status_code = 200
    mock_resp_empty.json.return_value = {"total": 0, "hits": []}

    mock_resp_retry = MagicMock()
    mock_resp_retry.status_code = 200
    mock_resp_retry.json.return_value = {
        "total": 1,
        "hits": [
            {
                "id": 12345,
                "duration": 8,
                "tags": "real estate, apartment",
                "videos": {
                    "large": {
                        "url": "https://pixabay.test/re_clip.mp4",
                        "width": 1920,
                        "height": 1080,
                        "size": 9000000,
                    }
                },
                "user": "RealtorClips",
            }
        ],
    }

    with patch("httpx.Client.get", side_effect=[mock_resp_empty, mock_resp_retry]) as mock_get:
        candidates = provider.search(
            "A modern luxury glass apartment with panoramic views.",
            primary_domain="real estate",
        )
        assert len(candidates) == 1
        assert candidates[0].clip_id == "pixabay_12345"
        assert mock_get.call_count == 2

        second_call_params = mock_get.call_args_list[1][1]["params"]
        # Verify the retry query kept 'real estate'
        assert "real estate" in second_call_params["q"]
