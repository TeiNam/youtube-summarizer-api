"""요청 데이터 모델 정의"""

from pydantic import BaseModel


class SummarizeRequest(BaseModel):
    """유튜브 영상 요약 요청 모델

    Attributes:
        url: 유튜브 영상 URL (필수)
        target_language: 번역 대상 언어 (기본값: 한국어)
    """

    url: str
    target_language: str = "ko"
