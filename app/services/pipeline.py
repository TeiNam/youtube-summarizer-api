"""처리 파이프라인 모듈

전체 요약 파이프라인을 오케스트레이션한다.
자막 추출 → (실패 시) 음성 인식 → 번역 → 요약 순서로 처리하며,
각 단계별로 작업 상태를 업데이트한다.
"""

import logging

from app.models.responses import TaskStatus
from app.services.audio_transcriber import transcribe_audio
from app.services.subtitle_extractor import extract_subtitles, fetch_video_title
from app.services.summary_engine import summarize_text, translate_text
from app.services.task_manager import TaskManager

logger = logging.getLogger(__name__)


async def process_summary(
    task_id: str,
    video_id: str,
    target_language: str,
    task_manager: TaskManager,
) -> None:
    """백그라운드에서 전체 요약 파이프라인을 실행한다.

    처리 흐름:
    1. 자막 추출 시도 (실패 시 음성 인식으로 폴백)
    2. 추출된 텍스트를 대상 언어로 번역
    3. 번역된 텍스트를 요약 및 핵심 포인트 추출
    4. 결과를 작업 상태에 저장

    Args:
        task_id: 작업 고유 ID
        video_id: 유튜브 비디오 ID
        target_language: 번역 대상 언어 코드
        task_manager: 작업 상태 관리자 인스턴스
    """
    extraction_method = "subtitle"

    try:
        # 1단계: 텍스트 추출 (자막 또는 음성 인식) + 영상 제목 가져오기
        task_manager.update_status(task_id, TaskStatus.EXTRACTING)
        logger.info("작업 %s: 자막 추출 시작 (비디오: %s)", task_id, video_id)

        video_title = await fetch_video_title(video_id)
        text = await extract_subtitles(video_id)

        if text is None:
            # 자막 추출 실패 시 음성 인식으로 폴백
            logger.info("작업 %s: 자막 없음, 음성 인식으로 폴백", task_id)
            extraction_method = "transcribe"
            text = await transcribe_audio(video_id)

        # 2단계: 번역
        task_manager.update_status(task_id, TaskStatus.TRANSLATING)
        logger.info("작업 %s: 번역 시작 (대상 언어: %s)", task_id, target_language)

        translated_text = await translate_text(text, target_language)

        # 3단계: 요약
        task_manager.update_status(task_id, TaskStatus.SUMMARIZING)
        logger.info("작업 %s: 요약 시작", task_id)

        summary_result = await summarize_text(translated_text)

        # 4단계: 완료 - 결과 저장
        result = {
            "video_title": video_title,
            "original_language": "auto",
            "extraction_method": extraction_method,
            "translated_text": translated_text,
            "summary": summary_result["summary"],
            "key_points": summary_result["key_points"],
        }

        task_manager.update_status(task_id, TaskStatus.COMPLETED, result=result)
        logger.info("작업 %s: 파이프라인 완료", task_id)

    except Exception as e:
        # 오류 발생 시 작업 상태를 failed로 변경
        error_message = str(e)
        logger.error(
            "작업 %s: 파이프라인 실패 - %s", task_id, error_message, exc_info=True
        )
        task_manager.update_status(
            task_id, TaskStatus.FAILED, error=error_message
        )
