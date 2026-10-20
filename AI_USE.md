# IncuBrix Engineering Assessment 3: AI Use Disclosure (AI_USE.md)
## Problem Statement 3 — Script and Voiceover to B-Roll Video Engine

**Author**: Harini V  
**Date**: October 2026  
**Repository**: [https://github.com/harini-1912/AutoBroll](https://github.com/harini-1912/AutoBroll)  

---

## 1. Overview and Philosophy

In accordance with IncuBrix Assessment 3 Guidelines ("Tools and resources: use anything — Disclose everything in `AI_USE.md`"), this document details the AI tools, models, coding assistants, and automated workflows utilized throughout the design, implementation, debugging, optimization, and documentation of the **AutoBroll** B-Roll Video Engine.

Every line of generated code was verified, executed, tested locally and on cloud staging environments (Render), and is fully understood by the author. The author is fully prepared to explain, walk through, debug, and modify any component of this codebase during the live screening session.

---

## 2. AI Coding Assistants & Tools Used

| Tool / Assistant | Provider / Model | Primary Use Cases | Areas of Contribution |
|---|---|---|---|
| **Antigravity Coding Assistant** | DeepMind / Anthropic Claude 3.7 Sonnet | Full-stack scaffolding, debugging, test harness, FFmpeg pipeline optimization | - FastAPI application architecture & schema design<br>- Asynchronous `JobRunner` with semaphore control<br>- FFmpeg filter graph construction (scale, crop, subtitles, concat)<br>- Test pack suite generation & benchmark scripting |
| **OpenAI ChatGPT (GPT-4o)** | OpenAI | Architectural brainstorming & prompt engineering | - Designing stock footage search query decomposition<br>- Designing 4-tier fallback hierarchy (real stock ➔ photo pan/zoom ➔ procedural ➔ lavfi)<br>- Structuring the `manifest.json` provenance schema |
| **GitHub Copilot** | GitHub / OpenAI Codex | Inline IDE code completion | - Boilerplate typing, docstrings, schema validations, Pydantic field definitions |

---

## 3. Specific Workflows & Tasks Delegated to AI

### 3.1 Architecture & Schema Design
- **Task**: Defining the REST API contract and Integration Standards.
- **AI Contribution**: Generated initial Pydantic models for `JobRequest`, `JobStatusResponse`, `Manifest`, and `ErrorDetail` conforming to RFC-7807 and IncuBrix UPPER_SNAKE_CASE error formats.
- **Human Verification**: Verified that endpoints strictly adhered to HTTP status codes (`202 Accepted` for async submission, `400 Bad Request` with `error.code` payload, `404 Not Found`).

### 3.2 FFmpeg Filter Graph & Alignment Engine
- **Task**: Complex multi-aspect ratio video scaling (`16:9`, `9:16`, `1:1`) and subtitle burn-in without aspect ratio distortion or audio cutoff.
- **AI Contribution**: Recommended using `scale=w:h:force_original_aspect_ratio=increase,crop=w:h` for clean center-cropping across portrait and square modes. Assisted with escaping path delimiters in FFmpeg subtitle filters (`subtitles='...':force_style='...'`).
- **Human Verification**: Diagnosed and fixed an audio cut-off issue caused by artificial scene duration clamps. Re-engineered `job_runner.py` and `ffmpeg_engine.py` to calculate exact audio durations using `ffprobe` and enforce $\sum \text{duration}(S_i) \equiv \text{duration}(\text{narration})$.

### 3.3 Memory Optimization for Cloud Deployment (512MB RAM)
- **Task**: Preventing Out-Of-Memory (OOM) kills on Render's free tier (0.1 vCPU, 512MB RAM limit).
- **AI Contribution**: Identified high memory consumption during 1080p rendering. Suggested 480p encoding profile (`-preset ultrafast -crf 28 -threads 1 -bufsize 500k`), disk-to-disk scene assembly, and Python garbage collection (`gc.collect()`).
- **Human Verification**: Profiled container memory during active rendering; verified peak RAM usage remained at ~285MB, well below the 512MB threshold.

### 3.4 Automated Testing & Batch Runner
- **Task**: Building comprehensive test packs and CLI batch runner.
- **AI Contribution**: Generated JSON input payloads across diverse domains (AI news, renewable energy, fitness, developer platforms, finance) and the `cli/batch_runner.py` script.
- **Human Verification**: Executed test suite (`pytest tests/`) achieving 100% pass rate across unit, integration, and mock tests.

---

## 4. Prompt Engineering & Iteration Log (Samples)

### Sample Prompt 1 (FFmpeg Audio-Video Synchronization):
> *"The output video ends prematurely before the audio narration completes because scene durations were capped at 4.0s. Provide a mathematical alignment model that probes the physical WAV file duration with ffprobe and ensures the sum of all scene durations matches the audio length down to the millisecond, so FFmpeg -shortest never cuts speech."*
- **Outcome**: Implemented proportional alignment scaling in `job_runner.py` and exact duration matching in `ffmpeg_engine.py`.

### Sample Prompt 2 (Resilient Stock Footage Fallback):
> *"Write a 4-tier fallback chain for footage fetching when Pixabay API rate limits or network issues occur: 1) Pixabay video, 2) Pixabay image with dynamic pan/zoom ken-burns effect, 3) Procedural PIL visual with topic typography, 4) Guaranteed lavfi solid color. Ensure zero jobs fail due to missing footage."*
- **Outcome**: Implemented `fallback_generator.py` and integrated into `stock_service.py`.

---

## 5. Candidate Attestation

1. **Authorship & Mastery**: I have reviewed, tested, and understand every line of code in this repository. No code was committed without local execution and validation.
2. **Live Session Preparedness**: During the live technical walkthrough and live coding exercise, I am capable of explaining any subsystem, modifying logic on demand, diagnosing injected bugs, and writing tests independently.
3. **Licensing & Compliance**: No proprietary, non-commercial, or unlicensed code was introduced through AI suggestions. All dependencies are open-source with permissive commercial licenses (MIT, Apache 2.0, BSD).
