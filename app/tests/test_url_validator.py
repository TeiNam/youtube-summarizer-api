"""유튜브 URL 검증기 테스트 모듈

Property 기반 테스트와 엣지 케이스 단위 테스트를 포함한다.
"""

import string

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.url_validator import validate_youtube_url


# --- 비디오 ID 생성 전략 ---
# 유튜브 비디오 ID는 11자리 영숫자 + '_' + '-' 문자로 구성
VIDEO_ID_ALPHABET = string.ascii_letters + string.digits + "_-"
video_id_strategy = st.text(
    alphabet=VIDEO_ID_ALPHABET, min_size=11, max_size=11
)


# =============================================================================
# Property 1: 유효한 URL에서 비디오 ID 추출
# Feature: youtube-summary-api, Property 1: 유효한 URL에서 비디오 ID 추출
# =============================================================================


class TestProperty1VideoIdExtraction:
    """Property 1: 임의의 11자리 영숫자 비디오 ID를 생성하여
    youtube.com/watch?v={id} 형식과 youtu.be/{id} 형식 모두에서
    동일한 비디오 ID가 추출되는지 검증한다.

    **Validates: Requirements 1.1, 1.4**
    """

    @given(video_id=video_id_strategy)
    @settings(max_examples=100)
    def test_watch_url_extracts_correct_id(self, video_id: str) -> None:
        """youtube.com/watch?v= 형식에서 비디오 ID가 정확히 추출되어야 한다."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        result = validate_youtube_url(url)
        assert result == video_id

    @given(video_id=video_id_strategy)
    @settings(max_examples=100)
    def test_short_url_extracts_correct_id(self, video_id: str) -> None:
        """youtu.be/ 형식에서 비디오 ID가 정확히 추출되어야 한다."""
        url = f"https://youtu.be/{video_id}"
        result = validate_youtube_url(url)
        assert result == video_id

    @given(video_id=video_id_strategy)
    @settings(max_examples=100)
    def test_both_formats_extract_same_id(self, video_id: str) -> None:
        """두 URL 형식에서 추출된 비디오 ID가 동일해야 한다."""
        watch_url = f"https://www.youtube.com/watch?v={video_id}"
        short_url = f"https://youtu.be/{video_id}"
        watch_result = validate_youtube_url(watch_url)
        short_result = validate_youtube_url(short_url)
        assert watch_result == short_result == video_id


# =============================================================================
# Property 2: 유효하지 않은 URL 거부
# Feature: youtube-summary-api, Property 2: 유효하지 않은 URL 거부
# =============================================================================


class TestProperty2InvalidUrlRejection:
    """Property 2: 유튜브 URL 패턴에 매칭되지 않는 임의의 문자열에 대해
    validate_youtube_url이 ValueError를 발생시키는지 검증한다.

    **Validates: Requirements 1.2**
    """

    @given(
        invalid_url=st.text().filter(
            lambda s: "youtube.com/watch?v=" not in s
            and "youtu.be/" not in s
        )
    )
    @settings(max_examples=100)
    def test_non_youtube_url_raises_value_error(
        self, invalid_url: str
    ) -> None:
        """유튜브 URL 패턴에 매칭되지 않는 문자열은 ValueError를 발생시켜야 한다."""
        with pytest.raises(ValueError):
            validate_youtube_url(invalid_url)


# =============================================================================
# 엣지 케이스 단위 테스트
# =============================================================================


class TestEdgeCases:
    """URL 검증기의 엣지 케이스 단위 테스트"""

    def test_empty_string_raises_value_error(self) -> None:
        """빈 문자열은 ValueError를 발생시켜야 한다."""
        with pytest.raises(ValueError):
            validate_youtube_url("")

    def test_whitespace_string_raises_value_error(self) -> None:
        """공백 문자열은 ValueError를 발생시켜야 한다."""
        with pytest.raises(ValueError):
            validate_youtube_url("   ")

    def test_other_site_url_raises_value_error(self) -> None:
        """다른 사이트 URL은 ValueError를 발생시켜야 한다."""
        with pytest.raises(ValueError):
            validate_youtube_url("https://www.google.com")
