# IncuBrix Backend

The core asynchronous video processing API and worker engine for IncuBrix.

## Overview
- Built with **FastAPI**, **SQLite**, and **Uvicorn**.
- Provides background job processing, multi-ratio video rendering (`16:9`, `9:16`, `1:1`), audio synthesis/transcription, footage sourcing with fallback chains, and manifest generation.

## Getting Started

### 1. Run Backend Locally
```bash
# Navigate to backend directory
cd backend

# Install dependencies
pip install -r requirements.txt

# Run server with hot reload
python run.py
```
Or directly via uvicorn:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 2. Run Test Suite
```bash
pytest
```

### 3. API Documentation
- Interactive Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI Specification: `http://localhost:8000/openapi.yaml`
