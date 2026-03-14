"""API 엔드포인트 정의 모듈

POST /summarize - 유튜브 영상 요약 작업 요청
GET /tasks/{task_id} - 작업 상태 조회
"""

import logging

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

from app.models.requests import SummarizeRequest
from app.models.responses import (
    ErrorDetail,
    ErrorResponse,
    TaskDetailResponse,
    TaskResponse,
    TaskStatus,
)
from app.services.pipeline import process_summary
from app.services.task_manager import TaskManager
from app.services.url_validator import validate_youtube_url

logger = logging.getLogger(__name__)

# 모듈 레벨 싱글톤 TaskManager 인스턴스
task_manager = TaskManager()

router = APIRouter()


@router.post("/summarize", status_code=202, response_model=TaskResponse)
async def summarize(request: SummarizeRequest, background_tasks: BackgroundTasks):
    """유튜브 영상 요약 작업을 요청한다.

    URL을 검증하고, 작업을 생성한 뒤 백그라운드에서 파이프라인을 실행한다.

    Args:
        request: 유튜브 URL과 대상 언어를 포함한 요청 모델
        background_tasks: FastAPI 백그라운드 태스크 매니저

    Returns:
        202 응답: 작업 ID와 상태 (pending)
        422 응답: 유효하지 않은 URL인 경우 오류 정보
    """
    # URL 검증 및 비디오 ID 추출
    try:
        video_id = validate_youtube_url(request.url)
    except ValueError as e:
        logger.warning("유효하지 않은 URL 요청: %s", request.url)
        error_response = ErrorResponse(
            error=ErrorDetail(code="INVALID_URL", message=str(e))
        )
        return JSONResponse(status_code=422, content=error_response.model_dump())

    # 작업 생성
    task_id = task_manager.create_task(request.url, request.target_language)
    logger.info("작업 생성 완료: %s (비디오: %s)", task_id, video_id)

    # 백그라운드에서 파이프라인 실행
    background_tasks.add_task(
        process_summary, task_id, video_id, request.target_language, task_manager
    )

    return TaskResponse(task_id=task_id, status=TaskStatus.PENDING)


@router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
async def get_task(task_id: str):
    """작업 ID로 상태를 조회한다.

    Args:
        task_id: 조회할 작업의 고유 ID

    Returns:
        200 응답: 작업 상세 정보 (상태, 결과, 오류)
        404 응답: 존재하지 않는 작업 ID인 경우 오류 정보
    """
    task = task_manager.get_task(task_id)

    if task is None:
        logger.warning("존재하지 않는 작업 ID 조회: %s", task_id)
        error_response = ErrorResponse(
            error=ErrorDetail(
                code="TASK_NOT_FOUND",
                message=f"작업을 찾을 수 없습니다: {task_id}",
            )
        )
        return JSONResponse(status_code=404, content=error_response.model_dump())

    # 오류 정보가 문자열인 경우 ErrorDetail로 변환
    error = None
    if task.get("error") is not None:
        error = ErrorDetail(code="PIPELINE_ERROR", message=task["error"])

    return TaskDetailResponse(
        task_id=task["task_id"],
        status=task["status"],
        result=task.get("result"),
        error=error,
    )
