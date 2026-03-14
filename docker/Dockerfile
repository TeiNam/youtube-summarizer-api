# YouTube Summary API - 프로덕션 Docker 이미지
# 멀티 스테이지 빌드로 최종 이미지 크기를 최소화한다.

# ============================================================
# Stage 1: 의존성 빌드
# ============================================================
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements-prod.txt .

# 가상환경에 의존성 설치 (빌드 캐시 최대 활용)
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements-prod.txt

# ============================================================
# Stage 2: 런타임 이미지
# ============================================================
FROM python:3.12-slim AS runtime

# 시스템 의존성 (yt-dlp에 ffmpeg 필요)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/* && \
    # 비루트 사용자 생성
    useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# builder 스테이지에서 가상환경만 복사
COPY --from=builder /opt/venv /opt/venv

# 가상환경 PATH 설정
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 애플리케이션 코드 복사
COPY app/ ./app/

# 비루트 사용자로 전환
USER appuser

EXPOSE 8000

# 헬스체크 (API_PREFIX 환경변수에 따라 경로 동적 적용)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; p=os.environ.get('API_PREFIX',''); urllib.request.urlopen(f'http://localhost:8000{p}/health')" || exit 1

# uvicorn 실행 (ROOT_PATH가 설정되면 --root-path 옵션 적용)
CMD sh -c "uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 ${ROOT_PATH:+--root-path $ROOT_PATH}"
