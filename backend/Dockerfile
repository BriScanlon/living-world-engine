FROM python:3.11-slim

ARG INSTALL_DOCLING=false
ARG INSTALL_WHISPER=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ffmpeg tippecanoe fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements-extras.txt /app/backend/
RUN pip install -r /app/backend/requirements.txt
RUN if [ "$INSTALL_DOCLING" = "true" ]; then \
        pip install --extra-index-url https://download.pytorch.org/whl/cpu "docling>=2.0"; \
    fi
RUN if [ "$INSTALL_WHISPER" = "true" ]; then pip install "faster-whisper>=1.0"; fi

COPY backend /app/backend
COPY scripts /app/scripts
COPY sample_data /app/sample_data

ENV ATLAS_DIR=/app/data/atlas
WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
