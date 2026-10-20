FROM python:3.11-slim

# Install system dependencies: ffmpeg, curl, git, ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set up non-root user 'user' with UID 1000
RUN useradd -m -u 1000 user

WORKDIR /home/user/app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code with user ownership
COPY --chown=user:user . .

# Ensure artifacts, data, storage, temp directories exist and /tmp has full write permissions
RUN mkdir -p /home/user/app/artifacts \
             /home/user/app/data \
             /home/user/app/storage \
             /home/user/app/temp && \
    chmod 1777 /tmp && \
    chown -R user:user /home/user/app

USER user

# Default environment variables optimized for free tiers (512MB RAM constraints)
ENV HOME=/home/user \
    PORT=8000 \
    FFMPEG_THREADS=2 \
    MAX_CONCURRENT_JOBS=1 \
    LOCAL_STORAGE_DIR=/home/user/app/artifacts \
    ARTIFACTS_DIR=/home/user/app/artifacts \
    PIXABAY_API_KEY=58096074-b7197b00a8b5a59653d71b5e3 \
    POLLINATIONS_API_KEY=sk_GAvJaRf8drlVtRQwBNdHUKWYMdgIFf70 \
    TTS_PROVIDER=gtts

EXPOSE 8000

# Dynamic port binding for Render, Koyeb, Railway, Hugging Face, or local docker
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
