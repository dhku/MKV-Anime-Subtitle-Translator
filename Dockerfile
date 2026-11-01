# syntax=docker/dockerfile:1
# MKV-Anime-Subtitle-Translator (NAS 용, CPU 전용, 기본 linux/amd64)
#
#   docker build -t mkv-anime-subtitle-translator .
#   docker build --platform linux/arm64 -t mkv-anime-subtitle-translator .   # ARM NAS (alass 소스 빌드)

ARG PYTHON_IMAGE=python:3.11-slim-bookworm

# ---------------------------------------------------------------- alass
# amd64 는 저장소의 정적 바이너리(lib/linux/alass-linux64)를 쓰고, 다른 아키텍처는 소스에서 빌드합니다.
FROM rust:1-slim-bookworm AS alass
ARG TARGETARCH
COPY lib/linux/alass-linux64 /tmp/alass-linux64
RUN if [ "${TARGETARCH:-amd64}" = "amd64" ]; then \
        install -D -m 0755 /tmp/alass-linux64 /out/alass; \
    else \
        cargo install alass-cli --locked --root /tmp/cargo \
        && install -D -m 0755 /tmp/cargo/bin/alass-cli /out/alass; \
    fi

# ---------------------------------------------------------------- Python 패키지 + Whisper 모델
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
# 실행할 때마다 받지 않도록 Whisper tiny 모델을 이미지에 넣어 둡니다.
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('tiny', device='cpu', compute_type='int8', download_root='/opt/whisper')"

# ---------------------------------------------------------------- ffmpeg (정적 바이너리)
FROM mwader/static-ffmpeg:7.1 AS ffmpeg

# ---------------------------------------------------------------- 최종 이미지
FROM ${PYTHON_IMAGE}

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ffmpeg /ffmpeg /ffprobe /usr/local/bin/
COPY --from=alass /out/alass /usr/local/bin/alass
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/whisper /opt/whisper

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_HOME=/app \
    WHISPER_MODEL_DIR=/opt/whisper \
    HF_HUB_OFFLINE=1 \
    PUID=1000 \
    PGID=1000 \
    TZ=Asia/Seoul

# 프로그램은 /opt/translator 에 두고, /app 은 설정 파일 볼륨으로 비워 둡니다.
COPY main.py /opt/translator/main.py
COPY kudong /opt/translator/kudong
COPY docker/entrypoint.sh /entrypoint.sh
# Windows 에서 체크아웃해 CRLF 가 들어가도 동작하도록 줄바꿈을 정리합니다.
RUN sed -i 's/\r$//' /entrypoint.sh \
 && chmod 0755 /entrypoint.sh \
 && chmod -R a+rX /opt/translator

WORKDIR /app
VOLUME ["/app", "/app/target", "/app/output", "/app/log"]

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "/opt/translator/main.py"]
