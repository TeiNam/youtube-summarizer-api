"""응답 데이터 모델 정의"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class TaskStatus(str, Enum):
    """작업 처리 상태 열거형

    각 단계는 파이프라인의 진행 상황을 나타낸다.
    """

    PENDING = "pending"  # 대기중
    EXTRACTING = "extracting"  # 텍스트 추출중
    TRANSLATING = "translating"  # 번역중
    SUMMARIZING = "summarizing"  # 요약중
    COMPLETED = "completed"  # 완료
    FAILED = "failed"  # 실패


class SummaryResult(BaseModel):
    """요약 결과 모델

    영상 요약 처리가 완료된 후 반환되는 결과 데이터.

    Attributes:
        video_title: 영상 제목
        original_language: 원본 언어 코드
        extraction_method: 텍스트 추출 방식 ("subtitle" 또는 "transcribe")
        translated_text: 번역된 전체 텍스트
        summary: 요약문
        key_points: 핵심 포인트 목록
    """

    video_title: str
    original_language: str
    extraction_method: str
    translated_text: str
    summary: str
    key_points: list[str]


class TaskResponse(BaseModel):
    """작업 생성 응답 모델

    요약 요청 접수 시 반환되는 응답.

    Attributes:
        task_id: 작업 고유 ID (UUID)
        status: 현재 작업 상태
    """

    task_id: str
    status: TaskStatus


class ErrorDetail(BaseModel):
    """오류 상세 정보 모델

    Attributes:
        code: 오류 코드 (예: INVALID_URL, TASK_NOT_FOUND)
        message: 사용자에게 표시할 오류 메시지
    """

    code: str
    message: str


class ErrorResponse(BaseModel):
    """오류 응답 모델

    모든 오류 응답의 최상위 래퍼.

    Attributes:
        error: 오류 상세 정보
    """

    error: ErrorDetail


class TaskDetailResponse(BaseModel):
    """작업 상세 조회 응답 모델

    작업 ID로 상태를 조회할 때 반환되는 응답.

    Attributes:
        task_id: 작업 고유 ID
        status: 현재 작업 상태
        result: 완료 시 요약 결과 (미완료 시 None)
        error: 실패 시 오류 정보 (성공 시 None)
    """

    task_id: str
    status: TaskStatus
    result: Optional[SummaryResult] = None
    error: Optional[ErrorDetail] = None
