"""처리 파이프라인 모듈

전체 요약 파이프라인을 오케스트레이션한다.
자막 추출 → (실패 시) 음성 인식 → 번역 → 요약 순서로 처리하며,
각 단계별로 작업 상태를 업데이트한다.
"""

import asyncio
import logging
import os

from app.models.responses import TaskStatus
from app.services.audio_transcriber import transcribe_audio
from app.services.subtitle_extractor import (
    extract_subtitles_with_language,
    fetch_video_metadata,
    is_subtitle_sufficient,
)
from app.services.summary_engine import summarize_text, translate_text
from app.services.task_manager import TaskManager

logger = logging.getLogger(__name__)

# 자막·음성 인식 모두 실패 시 사용자에게 보여줄 메시지
EXTRACTION_FAILED_MESSAGE = (
    "자막과 음성 인식을 모두 사용할 수 없는 영상입니다. "
    "자동 자막이 없거나 불완전하고, 오디오에서도 텍스트를 추출하지 못했습니다."
)

# 단계별 실패 메시지. 내부 예외 문자열(버킷명·모델 ID·경로·AWS 오류)을 그대로
# 노출하지 않으면서 어느 단계에서 멈췄는지는 알려 준다 — 상세 원인은 서버 로그의
# exc_info 에 남는다.
STAGE_FAILED_MESSAGES = {
    TaskStatus.EXTRACTING: "영상 정보를 가져오는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
    TaskStatus.TRANSLATING: "번역 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
    TaskStatus.SUMMARIZING: "요약 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
}
PIPELINE_FAILED_MESSAGE = "요약 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."

# 동시에 처리할 파이프라인 수 상한.
# 없으면 요청이 몰릴 때 다운로드·ffmpeg·Bedrock 이 한꺼번에 돌아 디스크·CPU·비용이
# 함께 튀고, 스레드풀도 고갈된다. 대기는 세마포어에서 순서를 기다리는 형태가 된다.
MAX_CONCURRENT_PIPELINES = int(os.environ.get("MAX_CONCURRENT_PIPELINES", "4"))

# 세마포어는 만들어진 이벤트 루프에 묶인다. 모듈 임포트 시점에는 루프가 없으므로
# 첫 사용 시점에 만들고, 루프별로 따로 보관한다 — 단일 객체를 재사용하면 테스트나
# 멀티 루프 환경에서 'bound to a different event loop' 로 작업이 PENDING 에 갇힌다.
_semaphores: "dict[asyncio.AbstractEventLoop, asyncio.Semaphore]" = {}


def _get_semaphore() -> asyncio.Semaphore:
    """현재 이벤트 루프의 동시 실행 제한 세마포어를 반환한다."""
    loop = asyncio.get_running_loop()
    semaphore = _semaphores.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_PIPELINES)
        _semaphores[loop] = semaphore
        # 닫힌 루프의 항목이 쌓이지 않게 정리한다
        for stale in [lp for lp in _semaphores if lp.is_closed()]:
            del _semaphores[stale]
    return semaphore


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

    동시 실행은 MAX_CONCURRENT_PIPELINES 로 제한한다 — 초과분은 순서를 기다린다.

    Args:
        task_id: 작업 고유 ID
        video_id: 유튜브 비디오 ID
        target_language: 번역 대상 언어 코드
        task_manager: 작업 상태 관리자 인스턴스
    """
    async with _get_semaphore():
        await _run_pipeline(task_id, video_id, target_language, task_manager)


async def _run_pipeline(
    task_id: str,
    video_id: str,
    target_language: str,
    task_manager: TaskManager,
) -> None:
    """실제 파이프라인 본문 (동시 실행 제한 안에서 호출된다)."""
    extraction_method = "subtitle"
    # 실패 메시지를 고르기 위해 현재 단계를 따라간다
    stage = TaskStatus.EXTRACTING

    try:
        # 1단계: 텍스트 추출 (자막 또는 음성 인식) + 영상 제목 가져오기
        task_manager.update_status(task_id, TaskStatus.EXTRACTING)
        logger.info("작업 %s: 자막 추출 시작 (비디오: %s)", task_id, video_id)

        video_title, duration, upload_date = await fetch_video_metadata(video_id)
        text, source_language = await extract_subtitles_with_language(video_id)

        # 자막이 없거나 불완전하면(영상 길이 대비 너무 짧으면) 음성 인식으로 폴백
        if not is_subtitle_sufficient(text, duration):
            logger.info(
                "작업 %s: 자막 없음/불충분, 음성 인식으로 폴백", task_id
            )
            extraction_method = "transcribe"
            # Transcribe 의 IdentifyLanguage 결과는 회수하지 않으므로 미상으로 둔다
            source_language = None
            try:
                text = await transcribe_audio(video_id)
            except Exception as e:
                # 폴백까지 실패하면 사용자용 메시지로 변환 (내부 에러 노출 방지)
                logger.error(
                    "작업 %s: 음성 인식 폴백 실패 - %s", task_id, e, exc_info=True
                )
                task_manager.update_status(
                    task_id, TaskStatus.FAILED, error=EXTRACTION_FAILED_MESSAGE
                )
                return

        # 2단계: 번역
        stage = TaskStatus.TRANSLATING
        task_manager.update_status(task_id, TaskStatus.TRANSLATING)
        logger.info("작업 %s: 번역 시작 (대상 언어: %s)", task_id, target_language)

        translated_text = await translate_text(text, target_language)

        # 3단계: 요약
        stage = TaskStatus.SUMMARIZING
        task_manager.update_status(task_id, TaskStatus.SUMMARIZING)
        logger.info("작업 %s: 요약 시작", task_id)

        summary_result = await summarize_text(translated_text)

        # 4단계: 완료 - 결과 저장
        # original_language 는 감지 결과를 쓴다. 자막 경로는 선택된 자막 언어를,
        # 음성 인식 경로는 Transcribe 의 IdentifyLanguage 결과를 알 수 없으므로
        # "auto" 로 둔다(응답 모델이 문자열을 요구한다).
        result = {
            "video_title": video_title,
            "upload_date": upload_date,
            "original_language": source_language or "auto",
            "extraction_method": extraction_method,
            "translated_text": translated_text,
            "summary": summary_result["summary"],
            "key_points": summary_result["key_points"],
        }

        task_manager.update_status(task_id, TaskStatus.COMPLETED, result=result)
        logger.info("작업 %s: 파이프라인 완료", task_id)

    except Exception as e:
        # 오류 발생 시 작업 상태를 failed로 변경.
        # 상세 원인은 로그에만 남기고 클라이언트에는 일반 메시지를 준다 —
        # 예외 문자열에 버킷명·모델 ID·파일 경로·AWS 오류가 섞여 나온다.
        logger.error(
            "작업 %s: 파이프라인 실패 (단계: %s) - %s",
            task_id,
            stage.value,
            e,
            exc_info=True,
        )
        task_manager.update_status(
            task_id,
            TaskStatus.FAILED,
            error=STAGE_FAILED_MESSAGES.get(stage, PIPELINE_FAILED_MESSAGE),
        )
