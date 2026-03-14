"""요약 엔진 테스트 모듈

번역/요약 성공 시나리오와 Bedrock 호출 실패 시나리오를 모킹하여 테스트한다.
"""

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.summary_engine import summarize_text, translate_text


# =============================================================================
# 번역 성공 시나리오
# =============================================================================


class TestTranslateTextSuccess:
    """Bedrock을 통한 번역 성공 테스트"""

    @pytest.mark.asyncio
    async def test_translate_returns_translated_text(self) -> None:
        """번역 요청 시 Bedrock 응답에서 번역된 텍스트를 반환해야 한다."""
        expected = "안녕하세요, 이것은 번역된 텍스트입니다."
        mock_response_body = json.dumps(
            {"content": [{"text": expected}]}
        ).encode("utf-8")

        with patch("app.services.summary_engine._get_bedrock_client") as mock_get:
            mock_client = MagicMock()
            mock_client.invoke_model.return_value = {
                "body": io.BytesIO(mock_response_body)
            }
            mock_get.return_value = mock_client

            result = await translate_text("Hello, this is translated text.", "ko")

        assert result == expected
        mock_client.invoke_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_translate_uses_default_language(self) -> None:
        """대상 언어를 지정하지 않으면 기본값 'ko'를 사용해야 한다."""
        mock_response_body = json.dumps(
            {"content": [{"text": "번역 결과"}]}
        ).encode("utf-8")

        with patch("app.services.summary_engine._get_bedrock_client") as mock_get:
            mock_client = MagicMock()
            mock_client.invoke_model.return_value = {
                "body": io.BytesIO(mock_response_body)
            }
            mock_get.return_value = mock_client

            result = await translate_text("Some text")

        assert result == "번역 결과"
        # invoke_model 호출 시 body에 'ko'가 포함되어 있는지 확인
        call_args = mock_client.invoke_model.call_args
        body_str = call_args[1]["body"] if "body" in call_args[1] else call_args[0][0]
        body_data = json.loads(body_str)
        assert "ko" in body_data["messages"][0]["content"]


# =============================================================================
# 요약 성공 시나리오
# =============================================================================


class TestSummarizeTextSuccess:
    """Bedrock을 통한 요약 성공 테스트"""

    @pytest.mark.asyncio
    async def test_summarize_returns_dict_with_summary_and_key_points(self) -> None:
        """요약 요청 시 summary와 key_points 키를 포함한 딕셔너리를 반환해야 한다."""
        summary_data = {
            "genre": "TECH",
            "one_line_summary": "파이썬 프로그래밍 기초부터 실전까지 다루는 영상이다.",
            "detailed_summary": "파이썬 기초 문법, 함수와 클래스 활용법, 실전 프로젝트 예제를 설명한다.",
            "key_insights": [
                "파이썬 기초 문법 설명",
                "함수와 클래스 활용법",
                "실전 프로젝트 예제",
            ],
            "keywords": [
                {"term": "Python", "description": "프로그래밍 언어"}
            ],
            "further_topics": ["Django 웹 프레임워크"],
        }
        # Bedrock 응답에 JSON 블록 포함
        response_text = f"```json\n{json.dumps(summary_data, ensure_ascii=False)}\n```"
        mock_response_body = json.dumps(
            {"content": [{"text": response_text}]}
        ).encode("utf-8")

        with patch("app.services.summary_engine._get_bedrock_client") as mock_get:
            mock_client = MagicMock()
            mock_client.invoke_model.return_value = {
                "body": io.BytesIO(mock_response_body)
            }
            mock_get.return_value = mock_client

            result = await summarize_text("파이썬 프로그래밍에 대한 긴 텍스트...")

        assert isinstance(result, dict)
        assert "summary" in result
        assert "key_points" in result
        # summary에 장르, 한줄 요약, 상세 내용이 포함되어야 한다
        assert "TECH" in result["summary"]
        assert summary_data["one_line_summary"] in result["summary"]
        assert summary_data["detailed_summary"] in result["summary"]
        # key_points에 핵심 인사이트가 담겨야 한다
        assert result["key_points"] == summary_data["key_insights"]

    @pytest.mark.asyncio
    async def test_summarize_handles_plain_json_response(self) -> None:
        """Bedrock이 코드 블록 없이 순수 JSON을 반환해도 정상 처리해야 한다."""
        summary_data = {
            "genre": "OTHER",
            "one_line_summary": "요약 내용입니다.",
            "detailed_summary": "상세 요약 내용입니다.",
            "key_insights": ["포인트 1", "포인트 2"],
            "keywords": [],
            "further_topics": [],
        }
        response_text = json.dumps(summary_data, ensure_ascii=False)
        mock_response_body = json.dumps(
            {"content": [{"text": response_text}]}
        ).encode("utf-8")

        with patch("app.services.summary_engine._get_bedrock_client") as mock_get:
            mock_client = MagicMock()
            mock_client.invoke_model.return_value = {
                "body": io.BytesIO(mock_response_body)
            }
            mock_get.return_value = mock_client

            result = await summarize_text("테스트 텍스트")

        assert "요약 내용입니다." in result["summary"]
        assert result["key_points"] == ["포인트 1", "포인트 2"]


# =============================================================================
# Bedrock 호출 실패 시나리오
# =============================================================================


class TestBedrockFailure:
    """Bedrock API 호출 실패 테스트"""

    @pytest.mark.asyncio
    async def test_translate_raises_runtime_error_on_bedrock_failure(self) -> None:
        """번역 중 Bedrock 호출 실패 시 RuntimeError를 발생시켜야 한다."""
        with patch("app.services.summary_engine._get_bedrock_client") as mock_get:
            mock_client = MagicMock()
            mock_client.invoke_model.side_effect = Exception("Bedrock 서비스 오류")
            mock_get.return_value = mock_client

            with pytest.raises(RuntimeError, match="번역 실패"):
                await translate_text("Hello", "ko")

    @pytest.mark.asyncio
    async def test_summarize_raises_runtime_error_on_bedrock_failure(self) -> None:
        """요약 중 Bedrock 호출 실패 시 RuntimeError를 발생시켜야 한다."""
        with patch("app.services.summary_engine._get_bedrock_client") as mock_get:
            mock_client = MagicMock()
            mock_client.invoke_model.side_effect = Exception("Bedrock 서비스 오류")
            mock_get.return_value = mock_client

            with pytest.raises(RuntimeError, match="요약 실패"):
                await summarize_text("테스트 텍스트")

    @pytest.mark.asyncio
    async def test_summarize_raises_on_invalid_json_response(self) -> None:
        """Bedrock이 유효하지 않은 JSON을 반환하면 RuntimeError를 발생시켜야 한다."""
        mock_response_body = json.dumps(
            {"content": [{"text": "이것은 JSON이 아닙니다"}]}
        ).encode("utf-8")

        with patch("app.services.summary_engine._get_bedrock_client") as mock_get:
            mock_client = MagicMock()
            mock_client.invoke_model.return_value = {
                "body": io.BytesIO(mock_response_body)
            }
            mock_get.return_value = mock_client

            with pytest.raises(RuntimeError, match="요약 결과 파싱 실패"):
                await summarize_text("테스트 텍스트")
