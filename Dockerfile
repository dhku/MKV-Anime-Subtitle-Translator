FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# OS 패키지: ffmpeg + 빌드 도구
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python 패키지
RUN pip install --upgrade pip setuptools wheel \
    && pip install \
        PyYAML \
        requests \
        beautifulsoup4 \
        natsort \
        tqdm \
        openai \
        ffmpeg-python \
        faster-whisper

WORKDIR /app

CMD ["python"]
