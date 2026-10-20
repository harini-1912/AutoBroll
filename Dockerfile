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

# Ensure artifacts, data, storage, and temp directories exist with ownership given to user 1000
RUN mkdir -p /home/user/app/artifacts \
             /home/user/app/data \
             /home/user/app/storage \
             /home/user/app/temp && \
    chown -R user:user /home/user/app

USER user

ENV HOME=/home/user \
    PORT=7860 \
    LOCAL_STORAGE_DIR=/home/user/app/artifacts \
    ARTIFACTS_DIR=/home/user/app/artifacts

EXPOSE 7860

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
