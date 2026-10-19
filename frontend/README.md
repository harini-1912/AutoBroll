# IncuBrix Frontend

The interactive user interface for the IncuBrix B-Roll Video Engine.

## Overview
- Single Page Application built with **React 18** and **Tailwind-inspired glassmorphism design**.
- Provides job submission (narration script, scenes list, or audio file/URL), live progress tracking, timeline preview, manifest inspection, and video downloads.

## Running the Frontend

### 1. Unified via Backend (Recommended)
The backend automatically hosts this frontend directory. When you run the backend service:
- Access the Web UI directly at: `http://localhost:8000/` or `http://localhost:8000/ui`

### 2. Standalone with Docker Compose
When running `docker compose up`:
- **Backend API & UI**: `http://localhost:8000`
- **Frontend Service**: `http://localhost:3000`

### 3. Local Static Server
You can also preview the frontend with any static file server:
```bash
# Using Python
cd frontend
python -m http.server 3000

# Using Node.js npx
npx serve .
```
