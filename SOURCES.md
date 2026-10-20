# IncuBrix Engineering Assessment 3: Sources & Licences (SOURCES.md)
## Problem Statement 3 — Script and Voiceover to B-Roll Video Engine

**Author**: Harini V  
**Date**: October 2026  
**Repository**: [https://github.com/harini-1912/AutoBroll](https://github.com/harini-1912/AutoBroll)  

---

## 1. Compliance Statement

In accordance with IncuBrix Assessment 3 Guidelines ("Disclose everything: List every model, API, library, dataset and media source in `SOURCES.md`"), this document catalogs all external dependencies, APIs, datasets, models, and media providers used by the **AutoBroll** engine.

All tools, libraries, and assets utilized in the default production path have been verified as **100% commercially usable** (permissive licenses: MIT, Apache 2.0, BSD, Pixabay Content License). No non-commercial (CC BY-NC) or restrictive AGPL dependencies are included in the runtime service.

---

## 2. Media & Asset Providers

| Provider / Source | Type of Asset | License Type | Commercial Use Permitted? | Cost / Tier |
|---|---|---|---|---|
| **Pixabay Video API** | Stock B-roll video footage (`.mp4`) | Pixabay Content License (free for commercial and non-commercial use) | **Yes** (No attribution required) | Free Tier (100 requests/minute, $0.00) |
| **Pixabay Image API** | High-resolution photography for pan/zoom fallback | Pixabay Content License | **Yes** (No attribution required) | Free Tier ($0.00) |
| **Procedural PIL / Lavfi** | Synthetic kinetic graphics & solid background frames | Generated in-process (Candidate-owned algorithm) | **Yes** (Full ownership) | Free ($0.00) |

---

## 3. Models & AI Services

| Model / Service | Version | Provider | Purpose | Commercial Terms | Cost Per Unit |
|---|---|---|---|---|---|
| **gTTS (Google Text-to-Speech)** | 2.5.4 | Google / Open-source wrapper | Voiceover narration synthesis | Open-source Python library wrapping public TTS endpoint | $0.00 |
| **Whisper (Optional)** | `base` / `small` | OpenAI | Automatic Speech Recognition (STT) for uploaded voiceovers | MIT License (Local weights execution) | $0.00 (Local compute) |
| **Mock TTS Adapter** | Internal | Candidate | Zero-network synthetic audio for unit & CI test suites | MIT License | $0.00 |

---

## 4. Software Libraries & Runtimes

| Library / Tool | Version | License | Purpose in Project |
|---|---|---|---|
| **Python** | 3.11 / 3.12 / 3.14 | PSF License | Core backend programming language |
| **FastAPI** | 0.115.x | MIT License | Asynchronous web framework & REST API |
| **Uvicorn** | 0.34.x | BSD-3-Clause | ASGI production HTTP server |
| **FFmpeg / FFprobe** | 6.0+ | LGPL v2.1+ | Media decoding, filtering, scaling, subtitle burn-in, concat demuxing |
| **Pydantic** | 2.10.x | MIT License | Data validation and schema enforcement |
| **HTTPX** | 0.28.x | BSD-3-Clause | Asynchronous HTTP client for external API requests |
| **AIOFiles** | 24.1.x | Apache 2.0 | Asynchronous non-blocking file I/O operations |
| **Pillow (PIL)** | 11.1.x | HPND License | In-memory procedural visual generation for fallback tier |
| **Pytest / Pytest-Asyncio** | 8.3.x | MIT License | Automated unit, integration, and async test framework |
| **OpenPyXL** | 3.1.x | MIT License | Automated Excel submission form population |

---

## 5. Third-Party Fonts & Typography

| Asset | Source | License | Commercial Permitted? |
|---|---|---|---|
| **Arial / DejaVu Sans** | Standard system package (`fonts-dejavu-core` in Docker) | Bitstream Vera / Public Domain | **Yes** |
| **Inter (Web)** | Google Fonts CDN | SIL Open Font License (OFL) 1.1 | **Yes** |

---

## 6. Datasets & Test Packs

| Dataset / Test Pack | Location | Origin / License | Description |
|---|---|---|---|
| **Test Pack Inputs** | `test_pack/inputs/` | Authored by Candidate (MIT) | 10 realistic test scripts across business, tech, robotics, fitness, and news |
| **Synthetic Test Audio** | Generated dynamically in test runner | Generated in-process (MIT) | 24kHz mono PCM WAV sine waves and TTS recordings for CI testing |

---

## 7. Secrets & Credential Management

- All external API keys (`PIXABAY_API_KEY`, etc.) are passed exclusively through environment variables (`.env` or Render Dashboard Environment Variables).
- **Zero secrets** are stored or committed in the git repository.
- A fallback mock mode operates seamlessly whenever `PIXABAY_API_KEY` is omitted or unset, ensuring zero hard dependencies during automated evaluation.
