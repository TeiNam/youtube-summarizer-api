# YouTube Summary API - 프로덕션 Docker 이미지
# 멀티 스테이지 빌드로 최종 이미지 크기를 최소화한다.

# ============================================================
# Stage 1: 의존성 빌드
# ============================================================
FROM python:3.13-slim AS builder

# uv 로 의존성을 설치한다. 버전 고정 — :latest 는 빌드 재현성을 깬다.
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /bin/uv

WORKDIR /build

# UV_PYTHON·UV_PYTHON_DOWNLOADS: 베이스 이미지의 인터프리터를 쓰게 강제한다.
# uv 가 자체 파이썬을 내려받으면 런타임 이미지에 없는 인터프리터를 가리키는 venv 가 된다.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=python3.13 \
    UV_PYTHON_DOWNLOADS=never

# 앱 소스보다 먼저 복사한다 — 소스만 바뀐 빌드는 이 레이어를 캐시에서 재사용한다.
COPY pyproject.toml uv.lock ./

# --frozen: uv.lock 에 박힌 정확한 버전만 설치하고 lock 재생성을 막는다.
# 잠금 없이 빌드하면 이미지마다 다른 버전이 들어간다(root_path 동작 차이처럼 실제로 물린다).
# --no-dev: 테스트 의존성(pytest 등)은 이미지에 넣지 않는다.
RUN uv sync --frozen --no-dev

# ============================================================
# Stage 2: 런타임 이미지
# ============================================================
FROM python:3.13-slim AS runtime

# 시스템 의존성 (yt-dlp에 ffmpeg 필요)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    # 비루트 사용자 생성
    useradd --create-home --shell /bin/bash appuser

# IAM Roles Anywhere 자격증명 헬퍼 — 장기 액세스 키 없이 임시 자격증명을 받는다.
# 이미지에 담아 어느 호스트에서 띄워도 같은 동작을 보장한다. 버전·SHA256 고정.
ARG AWS_SIGNING_HELPER_VERSION=1.8.4
ARG AWS_SIGNING_HELPER_SHA256=b7568acd6e1517a4e1adaee68d52bfd6284a0e5305677166cd83d43a07c815c9
RUN curl -fsSL -o /usr/local/bin/aws_signing_helper \
      "https://rolesanywhere.amazonaws.com/releases/${AWS_SIGNING_HELPER_VERSION}/X86_64/Linux/Amzn2023/aws_signing_helper" \
    && echo "${AWS_SIGNING_HELPER_SHA256}  /usr/local/bin/aws_signing_helper" | sha256sum -c - \
    && chmod 755 /usr/local/bin/aws_signing_helper

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
# exec로 sh 프로세스를 대체하여 PID 1이 uvicorn이 되도록 한다
CMD sh -c "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 ${ROOT_PATH:+--root-path $ROOT_PATH}"
