"""YouTube Summary API - FastAPI 앱 진입점

글로벌 예외 핸들러, 요청/응답 로깅 미들웨어를 포함한다.
"""

from dotenv import load_dotenv

# .env 파일에서 환경변수 로드 (다른 모듈보다 먼저 실행)
# override=False: Docker 환경변수 등 이미 설정된 값을 .env가 덮어쓰지 않는다
load_dotenv(override=False)

import logging
import json
import os
import sys
import time
from datetime import datetime, timezone

from botocore.exceptions import ConnectTimeoutError, ReadTimeoutError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.models.responses import ErrorDetail, ErrorResponse


class JsonFormatter(logging.Formatter):
    """구조화된 JSON 로그 포매터"""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # 예외 정보가 있으면 스택 트레이스 포함
        if record.exc_info and record.exc_info[0] is not None:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data, ensure_ascii=False)


def setup_logging() -> None:
    """구조화된 JSON 로깅 설정"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)


# 로깅 초기화
setup_logging()

logger = logging.getLogger(__name__)

# 서브 경로 프리픽스 (예: /yts/api)
API_PREFIX = os.environ.get("API_PREFIX", "")
# 리버스 프록시 root_path (Swagger UI 등에서 올바른 경로 표시용)
ROOT_PATH = os.environ.get("ROOT_PATH", "")

# FastAPI 앱 인스턴스 생성
app = FastAPI(
    title="YouTube Summary API",
    description="유튜브 영상 URL을 입력받아 자막 추출, 번역, 요약을 수행하는 REST API",
    version="0.1.0",
    root_path=ROOT_PATH,
)

# ---------------------------------------------------------------------------
# 글로벌 예외 핸들러
# ---------------------------------------------------------------------------


@app.exception_handler(ReadTimeoutError)
@app.exception_handler(ConnectTimeoutError)
async def timeout_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """AWS 서비스 타임아웃 예외 핸들러 (504 Gateway Timeout)

    boto3의 ReadTimeoutError, ConnectTimeoutError를 캡처하여
    504 상태 코드와 ErrorResponse 형식으로 응답한다.
    """
    logger.error(
        "AWS 서비스 타임아웃 발생: %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
    )
    error_response = ErrorResponse(
        error=ErrorDetail(
            code="SERVICE_TIMEOUT",
            message="AWS 서비스 요청 시간이 초과되었습니다",
        )
    )
    return JSONResponse(status_code=504, content=error_response.model_dump())


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """예상치 못한 예외 핸들러 (500 Internal Server Error)

    처리되지 않은 모든 예외를 캡처하여 500 상태 코드와
    ErrorResponse 형식으로 응답하고, 스택 트레이스를 로깅한다.
    """
    logger.error(
        "예상치 못한 오류 발생: %s %s - %s",
        request.method,
        request.url.path,
        str(exc),
        exc_info=exc,
    )
    error_response = ErrorResponse(
        error=ErrorDetail(
            code="INTERNAL_ERROR",
            message="내부 서버 오류가 발생했습니다",
        )
    )
    return JSONResponse(status_code=500, content=error_response.model_dump())


# ---------------------------------------------------------------------------
# API 키 인증 미들웨어
# ---------------------------------------------------------------------------

# 환경변수에서 API 키 로드
API_KEY = os.environ.get("API_KEY")

# 인증이 필요 없는 경로 목록 (API_PREFIX 반영)
_public_suffixes = ["/docs", "/openapi.json", "/redoc", "/health"]
PUBLIC_PATHS = {f"{API_PREFIX}{p}" for p in _public_suffixes}
# prefix 없는 원본 경로도 허용 (root_path 사용 시)
PUBLIC_PATHS.update(_public_suffixes)

if not API_KEY:
    logger.warning("API_KEY 환경변수가 설정되지 않았습니다. 인증이 비활성화됩니다.")


@app.middleware("http")
async def api_key_auth_middleware(request: Request, call_next):
    """API 키 인증 미들웨어

    X-API-Key 헤더의 값을 환경변수 API_KEY와 비교하여 인증한다.
    API_KEY가 설정되지 않은 경우 인증을 건너뛴다.
    """
    # API_KEY 미설정 시 인증 건너뜀
    if not API_KEY:
        return await call_next(request)

    # 공개 경로는 인증 건너뜀
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    # X-API-Key 헤더 확인
    request_api_key = request.headers.get("X-API-Key")

    if not request_api_key:
        logger.warning("API 키 누락: %s %s", request.method, request.url.path)
        error_response = ErrorResponse(
            error=ErrorDetail(code="MISSING_API_KEY", message="API 키가 필요합니다")
        )
        return JSONResponse(status_code=401, content=error_response.model_dump())

    if request_api_key != API_KEY:
        logger.warning("유효하지 않은 API 키: %s %s", request.method, request.url.path)
        error_response = ErrorResponse(
            error=ErrorDetail(
                code="INVALID_API_KEY", message="유효하지 않은 API 키입니다"
            )
        )
        return JSONResponse(status_code=401, content=error_response.model_dump())

    return await call_next(request)


# ---------------------------------------------------------------------------
# 요청/응답 로깅 미들웨어
# ---------------------------------------------------------------------------


@app.middleware("http")
async def request_response_logging_middleware(request: Request, call_next):
    """요청/응답 로깅 미들웨어

    모든 HTTP 요청과 응답에 대해 구조화된 JSON 로그를 기록한다.
    메서드, 경로, 상태 코드, 처리 시간을 포함한다.
    """
    start_time = time.time()

    # 응답 처리
    response = await call_next(request)

    # 처리 시간 계산 (밀리초)
    process_time_ms = round((time.time() - start_time) * 1000, 2)

    # 구조화된 로그 기록
    logger.info(
        "HTTP %s %s - %d (%.2fms)",
        request.method,
        request.url.path,
        response.status_code,
        process_time_ms,
        extra={
            "http_method": request.method,
            "http_path": request.url.path,
            "http_status": response.status_code,
            "process_time_ms": process_time_ms,
        },
    )

    return response


# API 라우터 등록 (프리픽스 적용)
app.include_router(router, prefix=API_PREFIX)

logger.info(
    "YouTube Summary API 앱이 초기화되었습니다 (prefix=%s, root_path=%s)",
    API_PREFIX or "(없음)",
    ROOT_PATH or "(없음)",
)
