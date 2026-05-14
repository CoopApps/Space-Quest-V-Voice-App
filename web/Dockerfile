# SQ5 Voice Studio — community web edition.
# Railway runs this Dockerfile; the persistent volume should be mounted at
# /data so contributions and the DB survive deploys.
FROM python:3.12-slim

# ffmpeg is needed for MP3 / M4A / AAC uploads.  Everything else uses
# soundfile (libsndfile) which is pulled in as a Python wheel.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so layer caches well.
COPY web/requirements.txt /app/web/requirements.txt
RUN pip install --no-cache-dir -r /app/web/requirements.txt

# Bring in both web/ and the voicestudio package it imports from.
COPY voicestudio /app/voicestudio
COPY web         /app/web

# Where the DB and contributions live on the persistent volume.
ENV SQ5_DATA_DIR=/data \
    SQ5_DB_PATH=/data/sq5_lines.db \
    PYTHONPATH=/app

EXPOSE 8000

# Shell form so $PORT (set by Railway) expands at runtime. Defaults to 8000
# for local docker-run smoke tests.
CMD uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8000}
