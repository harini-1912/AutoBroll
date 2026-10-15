# PROJECT_PLAN.md
## IncuBrix Engineering Assessment 3: Script & Voiceover to B-roll Video Engine

### Executive Summary
This document serves as the master specification, requirement traceability matrix, and execution plan for building the IncuBrix B-roll Video Engine. Every assessment must-have, level milestone, Integration Standard requirement, hard case, and deliverable is tracked here.

---

## 1. Requirement Traceability Matrix

### 1.1 Must-Haves (All 8 are Pass/Fail)
| # | Requirement | Specification | Implementation Location | Verification / Test | Evidence Location | Status |
|---|---|---|---|---|---|---|
| MH-1 | Both Inputs Work | Audio (`.mp3`, `.wav`, `.m4a` up to 10 min) and Text (TTS synthesis) | `app/services/speech.py`, `app/api/v1/endpoints/jobs.py` | `tests/test_speech.py`, `tests/test_api_jobs.py` | `test_pack/outputs/` | In Progress (Phase 1/2) |
| MH-2 | Meaningful Scenes & Narration Timing | Scene cuts on sentence/phrase boundaries; covers 100% of narration time; narration sets timing | `app/services/segmentation.py`, `app/rendering/timeline.py` | `tests/test_segmentation.py` | `outputs/{job_id}/timeline.json` | Planned (Phase 2) |
| MH-3 | Licence-Clean Commercial Footage | Source, licence, attribution, and link recorded per clip; commercial use allowed; no watermarks; AI clips flagged | `app/providers/footage/`, `app/services/license.py` | `tests/test_licensing.py` | `outputs/{job_id}/timeline.json` | Planned (Phase 2) |
| MH-4 | Correct, Consistent Video | Ratios: 16:9, 9:16, 1:1; normalized resolution, 30 fps, H.264/AAC, yuv420p; no black/frozen frames, no audio drift | `app/rendering/ffmpeg_engine.py`, `app/services/validator.py` | `tests/test_rendering.py` | `outputs/{job_id}/video_{ratio}.mp4` | Planned (Phase 2/3) |
| MH-5 | No Repeats & Semantic Matches | No clip used twice in one video; matches meaning, not literal keywords | `app/services/matcher.py`, `app/services/disambiguation.py` | `tests/test_matcher.py` | `outputs/{job_id}/timeline.json` | Planned (Phase 2) |
| MH-6 | Safe Output | No AI fake faces of real people; no fake brand logos; respectful footage for sensitive topics; avoid-list obeyed | `app/services/safety.py` | `tests/test_safety.py` | `outputs/{job_id}/manifest.json` | Planned (Phase 2) |
| MH-7 | Resilient Background Jobs | Background jobs with progress (0-100%); fallback provider chain if primary fails; job finishes reliably | `app/workers/job_runner.py`, `app/core/job_store.py` | `tests/test_resilience.py` | Logs & API response | In Progress (Phase 1) |
| MH-8 | Integration Standard & Live Demo | All 10 Integration Standard points implemented and live demo online | `app/api/`, `docker-compose.yml`, `openapi.yaml` | `tests/test_integration_standard.py` | Public URL & Demo Video | In Progress (Phase 1) |

---

### 1.2 Progression Levels
| Level | Criterion | Implementation Location | Test / Verification | Status |
|---|---|---|---|---|
| **Core** | All must-haves pass | Full Pipeline | Test suite pass | Planned |
| **Core** | 10 varied inputs (tutorial, news, promo, story, accents, 20s-5m) | `test_pack/inputs/` | Batch runner execution | Planned (Phase 3) |
| **Core** | Average relevance >= 3.5 / 5 | `app/services/evaluator.py` | Benchmark summary | Planned (Phase 3) |
| **Advanced** | >= 2 footage sources + measurable rule + reason in timeline | `app/providers/footage/`, `app/services/matcher.py` | `tests/test_footage_selection.py` | Planned (Phase 2) |
| **Advanced** | Swap a clip: fast partial re-render of affected scene only | `app/rendering/ffmpeg_engine.py`, `app/api/v1/endpoints/jobs.py` | `tests/test_clip_swap.py` | Planned (Phase 3) |
| **Advanced** | Visual cohesion: color/brightness normalization, sensible pacing | `app/rendering/ffmpeg_engine.py` | Video inspection & FFprobe | Planned (Phase 3) |
| **Advanced** | Accepts IncuBrix scene lists (`[{"text": "...", "visual_hint": "..."}]`) | `app/schemas/job.py`, `app/services/segmentation.py` | `tests/test_scene_list.py` | In Progress (Phase 1) |
| **Outstanding** | Measured relevance via vision-language scoring + human correlation | `app/services/evaluator.py` | Evaluation report | Planned (Phase 4) |
| **Outstanding** | Multilingual narration: Hindi & Tamil + English | `app/providers/tts/` | `tests/test_multilingual.py` | Planned (Phase 4) |
| **Outstanding** | Production numbers: cost/minute, render time, concurrency | `scripts/benchmark_concurrency.py` | `REPORT.md` | Planned (Phase 4) |
| **Outstanding** | >= 85% scenes rated 4 or 5 out of 5 | Evaluation dataset | `REPORT.md` & form | Planned (Phase 4) |

---

### 1.3 Integration Standard (10 Points, 2 Marks Each = 20 Marks)
| # | Requirement | Specification | Implementation Location | Evidence |
|---|---|---|---|---|
| IS-1 | One-command start and test | `docker compose up` starts everything (MinIO, app, mock storage); one command (`pytest`) runs tests with NO API keys; `.env.example` lists all settings | `docker-compose.yml`, `Dockerfile`, `.env.example`, `pytest.ini` | Docker logs & test output |
| IS-2 | Documented HTTP API | `Idempotency-Key` header on POST prevents duplicate execution; `openapi.yaml` (OpenAPI 3.1) matches running service; all routes under `/api/v1/` | `app/api/middleware.py`, `openapi.yaml`, `app/api/v1/` | API tests & openapi validation |
| IS-3 | Background jobs | Anything > 2s returns `202 Accepted` with `job_id`; `GET /api/v1/jobs/{job_id}` returns status (`queued`, `running`, `succeeded`, `failed`), progress 0-100%, result links; optional HMAC-SHA256 signed callback | `app/workers/job_runner.py`, `app/core/job_store.py`, `app/services/webhook.py` | Job status endpoint tests |
| IS-4 | Safe retries | Retry logic with exponential backoff on transient errors; idempotency cache preserves state | `app/core/retry.py` | Unit tests |
| IS-5 | Clear errors | Standard JSON format: `{"error": {"code": "...", "message": "...", "retryable": bool, "details": {}}}`; codes UPPER_SNAKE_CASE in README | `app/core/exceptions.py`, `app/schemas/error.py` | Error response tests |
| IS-6 | Configuration | All settings, paths, model names, and limits from env vars or config file. No hardcoded credentials/paths | `app/core/config.py` | Config review |
| IS-7 | Files in, files out | Inputs accepted as upload or URL; outputs stored in S3/MinIO compatible storage with download links | `app/storage/s3_storage.py`, `app/storage/local_storage.py` | Download endpoint tests |
| IS-8 | Logs and health | Structured JSON logs with timestamp, level, job_id, `X-Correlation-Id`; `GET /health` and `GET /ready` | `app/core/logging.py`, `app/api/endpoints/health.py` | Log output & health response |
| IS-9 | Output manifest | `manifest.json` generated for every job with models, providers, versions, settings, seed, timings, cost, warnings, licences | `app/services/manifest.py` | `manifest.json` verification |
| IS-10 | Batch runner | CLI tool processing test folder/file and saving outputs and logs to unified results folder | `cli/batch_runner.py` | CLI execution output |

---

### 1.4 Hard Cases Handling Strategy
1. **Abstract lines & statistics** ("trust is earned slowly", "sales grew 40%"): Disambiguation & concept mapping to metaphorical visuals (handshakes, rising graph overlays, architectural foundations).
2. **Real named person or brand**: Named Entity Recognition (NER) detects celebrities/brands -> flags warning in manifest, strictly avoids generating fake faces or counterfeit logos, maps to neutral conceptual stock.
3. **Words with double meanings** (bank, value, cloud, python, mouse): LLM/contextual disambiguation pipeline extracts contextual essence before query generation.
4. **Sad or sensitive news story**: Sentiment detection applies somber color grading, respectful pacing, and somber archival/documentary style footage (no upbeat or party stock).
5. **9:16 framing from 16:9 clip**: Saliency detection and blurred pillarbox/pan-and-scan so critical subjects aren't truncated awkwardly.
6. **Edge case audio/inputs**: Silent audio detection, music-only rejection/warning, invalid file formats, and empty scripts handled cleanly with descriptive `INVALID_INPUT` / `AUDIO_PROCESSING_ERROR` responses.
7. **Down/failing provider**: Seamless fallback chain (Primary Stock -> Secondary Stock -> Procedural AI Motion / Curated Local Archive).

---

## 2. Deliverables Checklist

- [ ] **A. Submission Form**: Complete with verified evidence links, hard-case table, and metrics.
  *(Note: Original Excel attachment from the invitation email is to be completed or provided; we maintain full compatibility with all required fields).*
- [ ] **B. Public Live Demo**: Working service deployed with rate limiting and accessible results.
- [ ] **C. Private GitHub Repository**: Clean commit history, reviewer access configured.
- [ ] **D. `README.md`**: Architecture, setup in <10 commands, environment, API examples, troubleshooting.
- [ ] **E. `openapi.yaml`**: Strict OpenAPI 3.1 specification.
- [ ] **F. `REPORT.md`**: 2–4 pages detailing design decisions, benchmark numbers, costs, and learnings.
- [ ] **G. `SOURCES.md`**: Full disclosure of third-party libraries, media sources, licences, and commercial clearance.
- [ ] **H. `AI_USE.md`**: Transparent disclosure of AI coding assistants and models.
- [ ] **I. Test Pack**: Automated test suite + test pack with inputs, outputs, logs, and evidence index.
- [ ] **J. Unedited Demo Video**: <=8 minute screen recording following the five brief requirements.
- [ ] **K. Video Pack**: Preserved video outputs across aspect ratios with timelines, captions, and manifests.

---

## 3. Implementation Phases (3-Day Schedule)

### Phase 1: Foundation & Async API Core (Completed)
- [x] Workspace inspection & Git repository initialization
- [x] Comprehensive `PROJECT_PLAN.md` created
- [x] Modular directory structure setup (`app/`, `cli/`, `tests/`, `test_pack/`)
- [x] Configuration management (`app/core/config.py`, `.env.example`)
- [x] Structured JSON logging with `X-Correlation-Id` and secret redaction
- [x] Standardized error handling & custom exceptions (`UPPER_SNAKE_CASE`)
- [x] Persistent job storage (SQLite + WAL mode + restart recovery)
- [x] Asynchronous background job worker with 11-stage pipeline tracking (0-100%)
- [x] Idempotency middleware (`Idempotency-Key` header handling and conflict protection)
- [x] Health and readiness endpoints (`GET /health`, `GET /ready`)
- [x] Job API endpoints (`POST /api/v1/broll/jobs`, `GET /api/v1/broll/jobs/{job_id}`, `/timeline`, `/manifest`)
- [x] Storage adapter (Local S3-compatible file storage + MinIO client)
- [x] Mock provider suite for zero-key deterministic testing
- [x] Strict `openapi.yaml` matching OpenAPI 3.1
- [x] Containerization: `Dockerfile` and `docker-compose.yml`
- [x] Automated test suite (33 passing unit & integration tests)
- [x] Interactive React 18 user interface (`static/index.html`)

### Phase 2: Speech, Segmentation & Footage Search (Current Milestone - Day 2 AM)
- [x] 11-stage worker orchestration with exponential backoff retries & provider fallback chain
- [ ] Production TTS provider adapters (gTTS, Edge-TTS, Indic voices)
- [ ] Real speech-to-text / sentence boundary alignment
- [ ] Semantic query expansion & double-meaning disambiguation
- [ ] Stock footage providers: Pexels API, Pixabay API, Curated Stock, AI Video/Motion
- [ ] Measurable footage selection rule & provider fallback chain
- [ ] Duplicate clip prevention & licensing verification

### Phase 3: Video Assembly, Subtitles, Clip-Swap & Manifest (Day 2 PM)
- [ ] FFmpeg assembly engine with 16:9, 9:16, 1:1 normalization (1080p, 30fps, H.264/AAC, yuv420p)
- [ ] Subtitle generation (`captions.srt` & burned-in video subtitles)
- [ ] Color/brightness normalization & sensible pacing
- [ ] Advanced Clip Swap endpoint (`POST .../scenes/{scene_id}/swap`) with fast partial re-render
- [ ] Comprehensive `timeline.json` and `manifest.json` generation
- [ ] End-to-end rendering pipeline validation

### Phase 4: Benchmarks, Hard Cases, Deliverables & Live Demo (Day 3)
- [ ] Batch runner CLI (`cli/batch_runner.py`)
- [ ] 10 varied test inputs execution + 5 hard-case tests
- [ ] Multilingual narration testing (Hindi, Tamil, English)
- [ ] Independent relevance scoring & correlation analysis
- [ ] Production metrics measurement (turnaround, cost/minute, concurrency)
- [ ] Web UI demo interface for external reviewers
- [ ] Documentation compilation: `README.md`, `REPORT.md`, `SOURCES.md`, `AI_USE.md`
- [ ] Final submission verification
