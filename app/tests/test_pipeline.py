"""처리 파이프라인 테스트 모듈

전체 파이프라인의 통합 단위 테스트를 모킹을 활용하여 수행한다.
- 자막 추출 경로 성공
- 자막 실패 시 음성 인식 폴백 성공
- 자막 + 음성 인식 모두 실패
- 번역 실패
- 요약 실패
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.models.responses import TaskStatus
from app.services.pipeline import process_summary
from app.services.task_manager import TaskManager


@pytest.fixture
def task_manager() -> TaskManager:
    """테스트용 TaskManager 인스턴스를 생성한다."""
    return TaskManager()


def _create_task(task_manager: TaskManager) -> str:
    """테스트용 작업을 생성하고 task_id를 반환한다."""
    return task_manager.create_task(
        url="https://www.youtube.com/watch?v=test123",
        target_language="ko",
    )


# =============================================================================
# 자막 추출 경로 성공 테스트
# =============================================================================


class TestPipelineSubtitleSuccess:
    """자막 추출 경로로 전체 파이프라인이 성공하는 시나리오"""

    @pytest.mark.asyncio
    @patch("app.services.pipeline.summarize_text", new_callable=AsyncMock)
    @patch("app.services.pipeline.translate_text", new_callable=AsyncMock)
    @patch("app.services.pipeline.extract_subtitles", new_callable=AsyncMock)
    async def test_full_pipeline_with_subtitle(
        self,
        mock_extract: AsyncMock,
        mock_translate: AsyncMock,
        mock_summarize: AsyncMock,
        task_manager: TaskManager,
    ) -> None:
        """자막이 존재하면 자막 텍스트로 번역 및 요약을 수행해야 한다."""
        task_id = _create_task(task_manager)

        # 모킹 설정
        mock_extract.return_value = "This is subtitle text."
        mock_translate.return_value = "이것은 자막 텍스트입니다."
        mock_summarize.return_value = {
            "summary": "자막 요약입니다.",
            "key_points": ["포인트 1", "포인트 2"],
        }

        await process_summary(task_id, "test123", "ko", task_manager)

        # 최종 상태 확인
        task = task_manager.get_task(task_id)
        assert task is not None
        assert task["status"] == TaskStatus.COMPLETED
        assert task["result"] is not None
        assert task["result"]["extraction_method"] == "subtitle"
        assert task["result"]["translated_text"] == "이것은 자막 텍스트입니다."
        assert task["result"]["summary"] == "자막 요약입니다."
        assert task["result"]["key_points"] == ["포인트 1", "포인트 2"]

        # 음성 인식은 호출되지 않아야 함
        mock_extract.assert_called_once_with("test123")
        mock_translate.assert_called_once_with("This is subtitle text.", "ko")
        mock_summarize.assert_called_once()


# =============================================================================
# 자막 실패 → 음성 인식 폴백 성공 테스트
# =============================================================================


class TestPipelineTranscribeFallback:
    """자막 추출 실패 시 음성 인식으로 폴백하는 시나리오"""

    @pytest.mark.asyncio
    @patch("app.services.pipeline.summarize_text", new_callable=AsyncMock)
    @patch("app.services.pipeline.translate_text", new_callable=AsyncMock)
    @patch("app.services.pipeline.transcribe_audio", new_callable=AsyncMock)
    @patch("app.services.pipeline.extract_subtitles", new_callable=AsyncMock)
    async def test_fallback_to_transcribe_on_subtitle_none(
        self,
        mock_extract: AsyncMock,
        mock_transcribe: AsyncMock,
        mock_translate: AsyncMock,
        mock_summarize: AsyncMock,
        task_manager: TaskManager,
    ) -> None:
        """자막이 None이면 음성 인식으로 폴백하고 extraction_method가 'transcribe'여야 한다."""
        task_id = _create_task(task_manager)

        # 자막 추출 실패 (None 반환), 음성 인식 성공
        mock_extract.return_value = None
        mock_transcribe.return_value = "Transcribed audio text."
        mock_translate.return_value = "음성 인식된 텍스트입니다."
        mock_summarize.return_value = {
            "summary": "음성 인식 요약입니다.",
            "key_points": ["포인트 A"],
        }

        await process_summary(task_id, "vid456", "ko", task_manager)

        # 최종 상태 확인
        task = task_manager.get_task(task_id)
        assert task is not None
        assert task["status"] == TaskStatus.COMPLETED
        assert task["result"]["extraction_method"] == "transcribe"
        assert task["result"]["translated_text"] == "음성 인식된 텍스트입니다."

        # 자막 추출과 음성 인식 모두 호출되어야 함
        mock_extract.assert_called_once_with("vid456")
        mock_transcribe.assert_called_once_with("vid456")


# =============================================================================
# 자막 + 음성 인식 모두 실패 테스트
# =============================================================================


class TestPipelineExtractionFailure:
    """자막 추출과 음성 인식 모두 실패하는 시나리오"""

    @pytest.mark.asyncio
    @patch("app.services.pipeline.transcribe_audio", new_callable=AsyncMock)
    @patch("app.services.pipeline.extract_subtitles", new_callable=AsyncMock)
    async def test_both_extraction_methods_fail(
        self,
        mock_extract: AsyncMock,
        mock_transcribe: AsyncMock,
        task_manager: TaskManager,
    ) -> None:
        """자막과 음성 인식 모두 실패하면 상태가 failed여야 한다."""
        task_id = _create_task(task_manager)

        mock_extract.return_value = None
        mock_transcribe.side_effect = RuntimeError("오디오 다운로드 실패")

        await process_summary(task_id, "fail_vid", "ko", task_manager)

        task = task_manager.get_task(task_id)
        assert task is not None
        assert task["status"] == TaskStatus.FAILED
        assert task["error"] is not None
        assert "오디오 다운로드 실패" in task["error"]


# =============================================================================
# 번역 실패 테스트
# =============================================================================


class TestPipelineTranslationFailure:
    """번역 단계에서 실패하는 시나리오"""

    @pytest.mark.asyncio
    @patch("app.services.pipeline.translate_text", new_callable=AsyncMock)
    @patch("app.services.pipeline.extract_subtitles", new_callable=AsyncMock)
    async def test_translation_failure_sets_failed_status(
        self,
        mock_extract: AsyncMock,
        mock_translate: AsyncMock,
        task_manager: TaskManager,
    ) -> None:
        """번역 실패 시 상태가 failed여야 한다."""
        task_id = _create_task(task_manager)

        mock_extract.return_value = "Some subtitle text."
        mock_translate.side_effect = RuntimeError("번역 실패: Bedrock 서비스 오류")

        await process_summary(task_id, "trans_fail", "ko", task_manager)

        task = task_manager.get_task(task_id)
        assert task is not None
        assert task["status"] == TaskStatus.FAILED
        assert "번역 실패" in task["error"]


# =============================================================================
# 요약 실패 테스트
# =============================================================================


class TestPipelineSummarizationFailure:
    """요약 단계에서 실패하는 시나리오"""

    @pytest.mark.asyncio
    @patch("app.services.pipeline.summarize_text", new_callable=AsyncMock)
    @patch("app.services.pipeline.translate_text", new_callable=AsyncMock)
    @patch("app.services.pipeline.extract_subtitles", new_callable=AsyncMock)
    async def test_summarization_failure_sets_failed_status(
        self,
        mock_extract: AsyncMock,
        mock_translate: AsyncMock,
        mock_summarize: AsyncMock,
        task_manager: TaskManager,
    ) -> None:
        """요약 실패 시 상태가 failed여야 한다."""
        task_id = _create_task(task_manager)

        mock_extract.return_value = "Subtitle text."
        mock_translate.return_value = "번역된 텍스트."
        mock_summarize.side_effect = RuntimeError("요약 실패: Bedrock 서비스 오류")

        await process_summary(task_id, "sum_fail", "ko", task_manager)

        task = task_manager.get_task(task_id)
        assert task is not None
        assert task["status"] == TaskStatus.FAILED
        assert "요약 실패" in task["error"]
