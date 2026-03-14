"""API 엔드포인트 테스트 모듈

Property 기반 테스트로 응답 모델의 필수 필드를 검증하고,
단위 테스트로 API 엔드포인트의 동작을 검증한다.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from app.api.routes import task_manager
from app.main import app
from app.models.responses import ErrorDetail, SummaryResult
from app.tests.conftest import PREFIX


# --- 테스트 클라이언트 ---
# conftest.py에서 설정한 테스트용 API 키를 헤더에 포함
AUTH_HEADERS = {"X-API-Key": "test-api-key-for-testing"}
client = TestClient(app)


# --- 전략 정의 ---
# SummaryResult 생성 전략
summary_result_strategy = st.builds(
    SummaryResult,
    video_title=st.text(min_size=1, max_size=50),
    original_language=st.sampled_from(["ko", "en", "ja", "zh", "es", "fr"]),
    extraction_method=st.sampled_from(["subtitle", "transcribe"]),
    translated_text=st.text(min_size=1, max_size=200),
    summary=st.text(min_size=1, max_size=200),
    key_points=st.lists(st.text(min_size=1, max_size=50), min_size=1, max_size=5),
)

# ErrorDetail 생성 전략
error_detail_strategy = st.builds(
    ErrorDetail,
    code=st.text(min_size=1, max_size=30),
    message=st.text(min_size=1, max_size=100),
)


# =============================================================================
# Property 4: 성공 응답 필수 필드 포함
# Feature: youtube-summary-api, Property 4: 성공 응답 필수 필드 포함
# =============================================================================


class TestProperty4SuccessResponseRequiredFields:
    """Property 4: 임의의 SummaryResult 직렬화 시 필수 필드 포함 검증.

    유효한 SummaryResult 객체에 대해, 직렬화된 JSON 응답에는
    video_title, original_language, extraction_method,
    translated_text, summary, key_points 필드가 모두 포함되어야 한다.

    **Validates: Requirements 5.2, 5.3, 6.1, 6.2, 6.4**
    """

    @given(result=summary_result_strategy)
    @settings(max_examples=100)
    def test_summary_result_contains_all_required_fields(
        self, result: SummaryResult
    ) -> None:
        """임의의 SummaryResult를 JSON 직렬화하면 필수 필드가 모두 포함되어야 한다."""
        serialized = result.model_dump()

        # 필수 필드 존재 검증
        required_fields = [
            "video_title",
            "original_language",
            "extraction_method",
            "translated_text",
            "summary",
            "key_points",
        ]
        for field in required_fields:
            assert field in serialized, f"필수 필드 '{field}'가 누락되었습니다"

        # key_points는 리스트 타입이어야 함
        assert isinstance(serialized["key_points"], list)


# =============================================================================
# Property 5: 오류 응답 필수 필드 포함
# Feature: youtube-summary-api, Property 5: 오류 응답 필수 필드 포함
# =============================================================================


class TestProperty5ErrorResponseRequiredFields:
    """Property 5: 임의의 ErrorDetail 직렬화 시 필수 필드 포함 검증.

    오류 응답에 대해, 직렬화된 JSON에는
    code와 message 필드가 모두 포함되어야 한다.

    **Validates: Requirements 6.3**
    """

    @given(error=error_detail_strategy)
    @settings(max_examples=100)
    def test_error_detail_contains_all_required_fields(
        self, error: ErrorDetail
    ) -> None:
        """임의의 ErrorDetail을 JSON 직렬화하면 필수 필드가 모두 포함되어야 한다."""
        serialized = error.model_dump()

        # 필수 필드 존재 검증
        assert "code" in serialized, "필수 필드 'code'가 누락되었습니다"
        assert "message" in serialized, "필수 필드 'message'가 누락되었습니다"


# =============================================================================
# 단위 테스트: API 엔드포인트 동작 검증
# =============================================================================


class TestPostSummarize:
    """POST /summarize 엔드포인트 단위 테스트"""

    def setup_method(self) -> None:
        """각 테스트 전 TaskManager 상태 초기화"""
        task_manager._tasks.clear()

    def test_valid_url_returns_202(self) -> None:
        """유효한 유튜브 URL 요청 시 202 응답을 반환해야 한다."""
        response = client.post(
            f"{PREFIX}/summarize",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 202
        data = response.json()
        assert "task_id" in data
        assert data["status"] == "pending"

    def test_invalid_url_returns_422(self) -> None:
        """유효하지 않은 URL 요청 시 422 응답을 반환해야 한다."""
        response = client.post(
            f"{PREFIX}/summarize",
            json={"url": "https://example.com/not-youtube"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 422
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "INVALID_URL"
        assert "message" in data["error"]

    def test_empty_url_returns_422(self) -> None:
        """빈 URL 요청 시 422 응답을 반환해야 한다."""
        response = client.post(
            f"{PREFIX}/summarize",
            json={"url": ""},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 422
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "INVALID_URL"


class TestGetTask:
    """GET /tasks/{task_id} 엔드포인트 단위 테스트"""

    def setup_method(self) -> None:
        """각 테스트 전 TaskManager 상태 초기화"""
        task_manager._tasks.clear()

    def test_existing_task_returns_200(self) -> None:
        """존재하는 작업 ID 조회 시 200 응답을 반환해야 한다."""
        # 작업 생성
        task_id = task_manager.create_task(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "ko"
        )

        response = client.get(f"{PREFIX}/tasks/{task_id}", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == task_id
        assert data["status"] == "pending"

    def test_nonexistent_task_returns_404(self) -> None:
        """존재하지 않는 작업 ID 조회 시 404 응답을 반환해야 한다."""
        response = client.get(f"{PREFIX}/tasks/nonexistent-task-id", headers=AUTH_HEADERS)
        assert response.status_code == 404
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "TASK_NOT_FOUND"
        assert "message" in data["error"]

    def test_completed_task_includes_result(self) -> None:
        """완료된 작업 조회 시 결과가 포함되어야 한다."""
        from app.models.responses import TaskStatus

        task_id = task_manager.create_task(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "ko"
        )
        result = {
            "video_title": "테스트 영상",
            "original_language": "en",
            "extraction_method": "subtitle",
            "translated_text": "번역된 텍스트",
            "summary": "요약문",
            "key_points": ["포인트1", "포인트2"],
        }
        task_manager.update_status(task_id, TaskStatus.COMPLETED, result=result)

        response = client.get(f"{PREFIX}/tasks/{task_id}", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["result"] is not None
        assert data["result"]["video_title"] == "테스트 영상"

    def test_failed_task_includes_error(self) -> None:
        """실패한 작업 조회 시 오류 정보가 포함되어야 한다."""
        from app.models.responses import TaskStatus

        task_id = task_manager.create_task(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "ko"
        )
        task_manager.update_status(
            task_id, TaskStatus.FAILED, error="파이프라인 처리 실패"
        )

        response = client.get(f"{PREFIX}/tasks/{task_id}", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert data["error"] is not None
        assert "code" in data["error"]
        assert "message" in data["error"]


class TestErrorResponseFormat:
    """오류 응답 형식 검증 단위 테스트"""

    def test_error_response_has_error_field(self) -> None:
        """오류 응답에는 error 필드가 포함되어야 한다."""
        response = client.post(
            f"{PREFIX}/summarize",
            json={"url": "invalid-url"},
            headers=AUTH_HEADERS,
        )
        data = response.json()
        assert "error" in data
        assert isinstance(data["error"], dict)

    def test_error_detail_has_code_and_message(self) -> None:
        """오류 상세에는 code와 message 필드가 포함되어야 한다."""
        response = client.get(f"{PREFIX}/tasks/does-not-exist", headers=AUTH_HEADERS)
        data = response.json()
        assert "code" in data["error"]
        assert "message" in data["error"]
