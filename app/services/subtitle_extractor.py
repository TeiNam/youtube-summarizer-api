"""유튜브 자막 추출 모듈

youtube-transcript-api 라이브러리를 활용하여 유튜브 영상의 자막을 추출한다.
원본 언어 자막을 우선적으로 선택하며, 자막이 없거나 추출에 실패하면 None을 반환한다.
"""

import logging
from typing import Optional

import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    CouldNotRetrieveTranscript,
    TranscriptsDisabled,
)

logger = logging.getLogger(__name__)


async def fetch_video_title(video_id: str) -> str:
    """유튜브 영상 제목을 가져온다.

    yt-dlp를 사용하여 영상 메타데이터에서 제목을 추출한다.
    실패 시 비디오 ID를 그대로 반환한다.

    Args:
        video_id: 유튜브 비디오 ID

    Returns:
        영상 제목. 실패 시 비디오 ID.
    """
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
        }
        url = f"https://www.youtube.com/watch?v={video_id}"
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get("title", video_id)
            logger.info("비디오 %s: 제목 추출 성공 - %s", video_id, title)
            return title
    except Exception as e:
        logger.warning("비디오 %s: 제목 추출 실패 - %s", video_id, e)
        return video_id


def select_preferred_language(
    available_languages: list[str],
    original_language: Optional[str] = None,
) -> Optional[str]:
    """사용 가능한 언어 목록에서 우선 언어를 선택한다.

    원본 언어가 목록에 포함되어 있으면 원본 언어를 반환하고,
    그렇지 않으면 목록의 첫 번째 언어를 반환한다.

    Args:
        available_languages: 사용 가능한 언어 코드 목록
        original_language: 원본 언어 코드 (예: "en", "ko")

    Returns:
        선택된 언어 코드. 목록이 비어있으면 None 반환.
    """
    if not available_languages:
        return None

    # 원본 언어가 목록에 포함되어 있으면 원본 언어 우선 선택
    if original_language and original_language in available_languages:
        return original_language

    # 원본 언어가 없거나 목록에 없으면 첫 번째 언어 선택
    return available_languages[0]


async def extract_subtitles(video_id: str) -> Optional[str]:
    """유튜브 영상에서 자막 텍스트를 추출한다.

    youtube-transcript-api를 사용하여 자막 데이터를 가져온다.
    여러 언어의 자막이 존재하면 원본 언어를 우선적으로 선택한다.
    자막이 없거나 추출에 실패하면 None을 반환하여 음성 인식기로 폴백한다.

    Args:
        video_id: 유튜브 비디오 ID (11자리)

    Returns:
        추출된 자막 텍스트. 자막이 없거나 오류 발생 시 None.
    """
    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)

        # 사용 가능한 자막의 언어 코드 수집 및 원본 언어 파악
        available_languages: list[str] = []
        original_language: Optional[str] = None

        for transcript in transcript_list:
            available_languages.append(transcript.language_code)
            # 자동 생성이 아닌 수동 자막이 있으면 원본 언어로 간주
            if not transcript.is_generated:
                original_language = transcript.language_code

        # 자동 생성 자막만 있는 경우 첫 번째 언어를 원본으로 간주
        if original_language is None and available_languages:
            original_language = available_languages[0]

        # 우선 언어 선택
        selected_language = select_preferred_language(
            available_languages, original_language
        )

        if selected_language is None:
            logger.warning("비디오 %s: 사용 가능한 자막이 없습니다", video_id)
            return None

        # 선택된 언어로 자막 가져오기
        fetched = api.fetch(video_id, languages=[selected_language])

        # 자막 스니펫을 하나의 텍스트로 결합
        text_parts = [snippet.text for snippet in fetched]
        full_text = " ".join(text_parts)

        logger.info(
            "비디오 %s: 자막 추출 성공 (언어: %s)", video_id, selected_language
        )
        return full_text

    except (TranscriptsDisabled, CouldNotRetrieveTranscript) as e:
        # 자막이 비활성화되었거나 가져올 수 없는 경우
        logger.warning("비디오 %s: 자막을 가져올 수 없습니다 - %s", video_id, e)
        return None
    except Exception as e:
        # 예상치 못한 오류
        logger.error(
            "비디오 %s: 자막 추출 중 오류 발생 - %s", video_id, e, exc_info=True
        )
        return None
