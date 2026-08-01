"""요청 데이터 모델 정의"""

from pydantic import BaseModel, Field, field_validator

# 번역 대상 언어 코드 허용 목록.
# 자유 문자열을 그대로 프롬프트에 넣으면 "무시하고 ~해라" 같은 지시를 실을 수 있고,
# 긴 문자열로 토큰을 낭비시킬 수도 있다. 프롬프트에 들어가는 값은 목록으로 묶는다.
ALLOWED_LANGUAGES = frozenset(
    {
        "ko",  # 한국어
        "en",  # 영어
        "ja",  # 일본어
        "zh",  # 중국어
        "es",  # 스페인어
        "fr",  # 프랑스어
        "de",  # 독일어
        "ru",  # 러시아어
        "pt",  # 포르투갈어
        "it",  # 이탈리아어
        "vi",  # 베트남어
        "th",  # 타이어
        "id",  # 인도네시아어
        "hi",  # 힌디어
        "ar",  # 아라비아어
    }
)

# URL 길이 상한 — 유튜브 URL 은 100자를 넘지 않는다. 여유를 둬서 자른다.
MAX_URL_LENGTH = 2048


class SummarizeRequest(BaseModel):
    """유튜브 영상 요약 요청 모델

    Attributes:
        url: 유튜브 영상 URL (필수)
        target_language: 번역 대상 언어 (기본값: 한국어). ALLOWED_LANGUAGES 로 제한.
    """

    # 빈 문자열은 여기서 막지 않는다 — validate_youtube_url 이 INVALID_URL 로 처리해
    # 오류 코드가 형식 검증(VALIDATION_ERROR)과 갈리지 않게 한다.
    url: str = Field(max_length=MAX_URL_LENGTH)
    target_language: str = "ko"

    @field_validator("target_language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        """허용 목록에 있는 언어 코드인지 확인한다 (대소문자 무시)."""
        normalized = value.strip().lower()
        if normalized not in ALLOWED_LANGUAGES:
            allowed = ", ".join(sorted(ALLOWED_LANGUAGES))
            raise ValueError(
                f"지원하지 않는 언어 코드입니다: {value!r}. 지원 언어: {allowed}"
            )
        return normalized
