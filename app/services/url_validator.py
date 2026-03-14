"""유튜브 URL 검증 및 비디오 ID 추출 모듈"""

import re


# 유튜브 URL 패턴 정규식
# youtube.com/watch?v=VIDEO_ID 형식
_YOUTUBE_WATCH_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?youtube\.com/watch\?.*v=([A-Za-z0-9_-]{11})(?:&|$)"
)

# youtu.be/VIDEO_ID 형식
_YOUTUBE_SHORT_PATTERN = re.compile(
    r"(?:https?://)?youtu\.be/([A-Za-z0-9_-]{11})(?:\?|$)"
)


def validate_youtube_url(url: str) -> str:
    """유튜브 URL을 검증하고 비디오 ID를 반환한다.

    Args:
        url: 검증할 유튜브 URL 문자열

    Returns:
        추출된 11자리 비디오 ID

    Raises:
        ValueError: URL이 빈 문자열이거나, 공백 문자열이거나,
                    유효한 유튜브 URL 형식이 아닌 경우
    """
    # 빈 문자열 또는 공백 문자열 검증
    if not url or not url.strip():
        raise ValueError("URL이 비어있거나 공백입니다")

    # youtube.com/watch?v= 형식 매칭
    match = _YOUTUBE_WATCH_PATTERN.search(url)
    if match:
        return match.group(1)

    # youtu.be/ 형식 매칭
    match = _YOUTUBE_SHORT_PATTERN.search(url)
    if match:
        return match.group(1)

    # 어떤 패턴에도 매칭되지 않으면 ValueError 발생
    raise ValueError(f"유효하지 않은 유튜브 URL입니다: {url}")
