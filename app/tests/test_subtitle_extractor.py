"""자막 추출기 테스트 모듈

Property 기반 테스트와 단위 테스트를 포함한다.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.subtitle_extractor import select_preferred_language


# --- 언어 코드 생성 전략 ---
# ISO 639-1 형식의 2~5자리 소문자 언어 코드 생성
language_code_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz", min_size=2, max_size=5
).filter(lambda s: len(s) >= 2)


# =============================================================================
# Property 3: 원본 언어 자막 우선 선택
# Feature: youtube-summary-api, Property 3: 원본 언어 자막 우선 선택
# =============================================================================


class TestProperty3PreferredLanguageSelection:
    """Property 3: 임의의 언어 코드 목록과 원본 언어가 주어졌을 때,
    원본 언어가 목록에 포함되어 있으면 자막 선택 함수는 항상 원본 언어를 반환해야 한다.

    **Validates: Requirements 2.3**
    """

    @given(
        other_languages=st.lists(language_code_strategy, min_size=0, max_size=10),
        original_language=language_code_strategy,
        insert_position=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=100)
    def test_original_language_always_selected_when_present(
        self,
        other_languages: list[str],
        original_language: str,
        insert_position: int,
    ) -> None:
        """원본 언어가 목록에 포함되어 있으면 항상 원본 언어가 선택되어야 한다."""
        # 원본 언어를 목록의 임의 위치에 삽입하여 포함시킴
        languages = list(other_languages)
        pos = insert_position % (len(languages) + 1)
        languages.insert(pos, original_language)

        result = select_preferred_language(languages, original_language)
        assert result == original_language


# =============================================================================
# 단위 테스트: 자막 추출 함수
# =============================================================================

from unittest.mock import MagicMock, patch

from app.services.subtitle_extractor import extract_subtitles


class TestExtractSubtitlesSuccess:
    """자막이 존재하는 경우 텍스트 추출 성공 테스트"""

    @pytest.mark.asyncio
    async def test_extract_subtitles_returns_text(self) -> None:
        """자막이 존재하면 결합된 텍스트를 반환해야 한다."""
        # 모킹: TranscriptList 이터레이션용 Transcript 객체
        mock_transcript = MagicMock()
        mock_transcript.language_code = "en"
        mock_transcript.is_generated = False

        # 모킹: TranscriptList
        mock_transcript_list = MagicMock()
        mock_transcript_list.__iter__ = MagicMock(
            return_value=iter([mock_transcript])
        )

        # 모킹: FetchedTranscript (스니펫 이터레이션)
        mock_snippet_1 = MagicMock()
        mock_snippet_1.text = "Hello world"
        mock_snippet_2 = MagicMock()
        mock_snippet_2.text = "this is a test"

        mock_fetched = MagicMock()
        mock_fetched.__iter__ = MagicMock(
            return_value=iter([mock_snippet_1, mock_snippet_2])
        )

        # YouTubeTranscriptApi 모킹
        with patch(
            "app.services.subtitle_extractor.YouTubeTranscriptApi"
        ) as mock_api_class:
            mock_api = MagicMock()
            mock_api.list.return_value = mock_transcript_list
            mock_api.fetch.return_value = mock_fetched
            mock_api_class.return_value = mock_api

            result = await extract_subtitles("test_vid_id")

        assert result == "Hello world this is a test"
        mock_api.list.assert_called_once_with("test_vid_id")
        mock_api.fetch.assert_called_once_with("test_vid_id", languages=["en"])


class TestExtractSubtitlesNoSubtitles:
    """자막이 없는 경우 None 반환 테스트"""

    @pytest.mark.asyncio
    async def test_transcripts_disabled_returns_none(self) -> None:
        """자막이 비활성화된 경우 None을 반환해야 한다."""
        from youtube_transcript_api._errors import TranscriptsDisabled

        with patch(
            "app.services.subtitle_extractor.YouTubeTranscriptApi"
        ) as mock_api_class:
            mock_api = MagicMock()
            mock_api.list.side_effect = TranscriptsDisabled("test_vid_id")
            mock_api_class.return_value = mock_api

            result = await extract_subtitles("test_vid_id")

        assert result is None

    @pytest.mark.asyncio
    async def test_no_transcript_found_returns_none(self) -> None:
        """자막을 찾을 수 없는 경우 None을 반환해야 한다."""
        from youtube_transcript_api._errors import CouldNotRetrieveTranscript

        with patch(
            "app.services.subtitle_extractor.YouTubeTranscriptApi"
        ) as mock_api_class:
            mock_api = MagicMock()
            mock_api.list.side_effect = CouldNotRetrieveTranscript("test_vid_id")
            mock_api_class.return_value = mock_api

            result = await extract_subtitles("test_vid_id")

        assert result is None


class TestExtractSubtitlesError:
    """추출 오류 발생 시 None 반환 테스트"""

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_none(self) -> None:
        """예상치 못한 오류 발생 시 None을 반환해야 한다."""
        with patch(
            "app.services.subtitle_extractor.YouTubeTranscriptApi"
        ) as mock_api_class:
            mock_api = MagicMock()
            mock_api.list.side_effect = RuntimeError("네트워크 오류")
            mock_api_class.return_value = mock_api

            result = await extract_subtitles("test_vid_id")

        assert result is None
