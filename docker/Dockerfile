# YouTube Summary API - 프로덕션 Docker 이미지
# 멀티 스테이지 빌드로 최종 이미지 크기를 최소화한다.

FROM python:3.12-slim AS base

# 시스템 의존성 설치 (yt-dlp에 ffmpeg 필요)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성 먼저 설치 (캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 코드 복사
COPY app/ ./app/

# 비루트 사용자로 실행
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

# uvicorn으로 서버 실행
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
