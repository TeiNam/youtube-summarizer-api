"""작업 관리자 테스트 모듈

Property 기반 테스트로 TaskManager의 상태 관리 정확성을 검증한다.
"""

import uuid

from hypothesis import given, settings
from hypothesis import strategies as st

from app.models.responses import TaskStatus
from app.services.task_manager import TaskManager


# --- 전략 정의 ---
# 유효한 TaskStatus 값 생성 전략
task_status_strategy = st.sampled_from(list(TaskStatus))

# 요약 결과 딕셔너리 생성 전략
summary_result_strategy = st.fixed_dictionaries(
    {
        "video_title": st.text(min_size=1, max_size=50),
        "original_language": st.text(min_size=2, max_size=5),
        "extraction_method": st.sampled_from(["subtitle", "transcribe"]),
        "translated_text": st.text(min_size=1, max_size=200),
        "summary": st.text(min_size=1, max_size=200),
        "key_points": st.lists(st.text(min_size=1, max_size=50), min_size=1, max_size=5),
    }
)

# 오류 메시지 생성 전략
error_message_strategy = st.text(min_size=1, max_size=100)

# 유튜브 URL 생성 전략
youtube_url_strategy = st.text(min_size=5, max_size=100).map(
    lambda s: f"https://www.youtube.com/watch?v={s}"
)

# 대상 언어 생성 전략
target_language_strategy = st.sampled_from(["ko", "en", "ja", "zh", "es", "fr"])


# =============================================================================
# Property 6: 작업 상태 조회 정확성
# Feature: youtube-summary-api, Property 6: 작업 상태 조회 정확성
# =============================================================================


class TestProperty6TaskStatusAccuracy:
    """Property 6: 임의의 상태 업데이트 후 조회 시 정확한 상태 반환 검증.

    TaskManager에 상태를 업데이트한 후 해당 작업 ID로 조회하면
    업데이트된 상태가 정확히 반환되어야 하며,
    상태가 completed인 경우 result가 포함되어야 한다.

    **Validates: Requirements 7.2, 7.3**
    """

    @given(
        status=task_status_strategy,
        url=youtube_url_strategy,
        target_language=target_language_strategy,
        result=summary_result_strategy,
        error_msg=error_message_strategy,
    )
    @settings(max_examples=100)
    def test_status_update_and_query_accuracy(
        self,
        status: TaskStatus,
        url: str,
        target_language: str,
        result: dict,
        error_msg: str,
    ) -> None:
        """임의의 상태로 업데이트 후 조회하면 정확한 상태가 반환되어야 한다."""
        manager = TaskManager()
        task_id = manager.create_task(url, target_language)

        # 상태에 따라 적절한 데이터와 함께 업데이트
        if status == TaskStatus.COMPLETED:
            manager.update_status(task_id, status, result=result)
        elif status == TaskStatus.FAILED:
            manager.update_status(task_id, status, error=error_msg)
        else:
            manager.update_status(task_id, status)

        # 조회 결과 검증
        task = manager.get_task(task_id)
        assert task is not None
        assert task["status"] == status
        assert task["task_id"] == task_id

        # completed 상태인 경우 result가 포함되어야 함
        if status == TaskStatus.COMPLETED:
            assert task["result"] is not None
            assert task["result"] == result

        # failed 상태인 경우 error가 포함되어야 함
        if status == TaskStatus.FAILED:
            assert task["error"] is not None
            assert task["error"] == error_msg


# =============================================================================
# Property 7: 미등록 작업 ID 조회 시 None 반환
# Feature: youtube-summary-api, Property 7: 미등록 작업 ID 조회 시 None 반환
# =============================================================================


class TestProperty7UnregisteredTaskReturnsNone:
    """Property 7: 임의의 UUID로 미등록 작업 조회 시 None 반환 검증.

    TaskManager에 등록되지 않은 임의의 UUID에 대해
    get_task는 None을 반환해야 한다.

    **Validates: Requirements 7.4**
    """

    @given(random_uuid=st.uuids())
    @settings(max_examples=100)
    def test_unregistered_task_id_returns_none(
        self, random_uuid: uuid.UUID
    ) -> None:
        """등록되지 않은 임의의 UUID로 조회하면 None이 반환되어야 한다."""
        manager = TaskManager()
        task_id = str(random_uuid)
        result = manager.get_task(task_id)
        assert result is None
