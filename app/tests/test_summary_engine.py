"""요약 엔진 테스트 모듈

번역/요약 성공 시나리오와 Bedrock 호출 실패 시나리오를 모킹하여 테스트한다.
"""

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.summary_engine import (
    PROMPTS_DIR,
    _parse_sections,
    summarize_text,
    translate_text,
)


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


def _mock_bedrock(response_text: str) -> dict:
    """섹션 형식 응답을 담은 Bedrock invoke_model 반환값을 만든다."""
    body = json.dumps({"content": [{"text": response_text}]}).encode("utf-8")
    return {"body": io.BytesIO(body)}


def test_prompt_format_matches_parser() -> None:
    """프롬프트가 지시하는 구분자를 파서가 실제로 인식해야 한다.

    프롬프트(모델이 따르는 계약)와 파서는 서로 다른 파일에 있고, 어긋나도
    다른 테스트는 통과한다 — 모킹된 응답은 테스트가 직접 쓰기 때문이다.
    형식만 바꾸고 배포하면 운영에서 전 건 실패로만 드러난다.
    """
    prompt = (PROMPTS_DIR / "summarize.md").read_text(encoding="utf-8")

    assert set(_parse_sections(prompt)) == {
        "GENRE",
        "ONE_LINE",
        "DETAILED",
        "INSIGHTS",
        "KEYWORDS",
        "FURTHER",
    }


class TestSummarizeTextSuccess:
    """Bedrock을 통한 요약 성공 테스트"""

    @pytest.mark.asyncio
    async def test_summarize_returns_dict_with_summary_and_key_points(self) -> None:
        """요약 요청 시 summary와 key_points 키를 포함한 딕셔너리를 반환해야 한다."""
        response_text = """===GENRE===
TECH
===ONE_LINE===
파이썬 프로그래밍 기초부터 실전까지 다루는 영상이다.
===DETAILED===
## 기초 문법
파이썬 기초 문법, 함수와 클래스 활용법, 실전 프로젝트 예제를 설명한다.
===INSIGHTS===
- 파이썬 기초 문법 설명
- 함수와 클래스 활용법
- 실전 프로젝트 예제
===KEYWORDS===
- **Python**: 프로그래밍 언어
===FURTHER===
- Django 웹 프레임워크
"""

        with patch("app.services.summary_engine._get_bedrock_client") as mock_get:
            mock_client = MagicMock()
            mock_client.invoke_model.return_value = _mock_bedrock(response_text)
            mock_get.return_value = mock_client

            result = await summarize_text("파이썬 프로그래밍에 대한 긴 텍스트...")

        assert isinstance(result, dict)
        # summary에 장르, 한줄 요약, 상세 내용, 키워드, 추가 주제가 모두 담겨야 한다
        assert "TECH" in result["summary"]
        assert "파이썬 프로그래밍 기초부터 실전까지" in result["summary"]
        assert "## 기초 문법" in result["summary"]
        assert "- **Python**: 프로그래밍 언어" in result["summary"]
        assert "- Django 웹 프레임워크" in result["summary"]
        # key_points에 핵심 인사이트가 목록 표식 없이 담겨야 한다
        assert result["key_points"] == [
            "파이썬 기초 문법 설명",
            "함수와 클래스 활용법",
            "실전 프로젝트 예제",
        ]

    @pytest.mark.asyncio
    async def test_quotes_and_backslashes_survive_verbatim(self) -> None:
        """본문에 따옴표·역슬래시·개행·코드블록이 그대로 들어와도 깨지지 않아야 한다.

        JSON 계약이던 시절 운영에서 터진 지점이다 — 모델이 4KB 넘는 마크다운을
        JSON 문자열로 직렬화하면서 따옴표 하나를 escape 하지 않아 응답 전체가
        버려졌다(`Expecting ',' delimiter: line 4 column 4123`). 구분자 형식은
        본문을 그대로 슬라이스하므로 escape 자체가 존재하지 않는다.
        """
        quoted = '삼성전자는 "AI 수요가 꺾이지 않는다"는 전제 위에 서 있다.'
        path = r"경로는 C:\temp\data 처럼 역슬래시도 나온다."
        response_text = f"""===GENRE===
FINANCE
===ONE_LINE===
{quoted}
===DETAILED===
## 반도체와 코스피
{quoted}
{path}

```python
print("코드블록도 그대로")
```
===INSIGHTS===
- 근거는 "직접 인용"이다.
"""

        with patch("app.services.summary_engine._get_bedrock_client") as mock_get:
            mock_client = MagicMock()
            mock_client.invoke_model.return_value = _mock_bedrock(response_text)
            mock_get.return_value = mock_client

            result = await summarize_text("코스피 관련 자막")

        assert quoted in result["summary"], "따옴표가 든 문장이 보존되어야 한다"
        assert r"C:\temp\data" in result["summary"], "역슬래시가 보존되어야 한다"
        assert 'print("코드블록도 그대로")' in result["summary"]
        assert result["key_points"] == ['근거는 "직접 인용"이다.']

    @pytest.mark.asyncio
    async def test_genre_is_narrowed_to_allowed_value(self) -> None:
        """GENRE 섹션에 군더더기가 붙어 와도 허용된 장르만 남아야 한다."""
        response_text = """===GENRE===
장르: LECTURE (강의/교육)
===DETAILED===
본문
"""

        with patch("app.services.summary_engine._get_bedrock_client") as mock_get:
            mock_client = MagicMock()
            mock_client.invoke_model.return_value = _mock_bedrock(response_text)
            mock_get.return_value = mock_client

            result = await summarize_text("테스트 텍스트")

        assert "🏷️ 장르: LECTURE" in result["summary"]
        assert "강의/교육" not in result["summary"]


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
    async def test_summarize_raises_when_sections_missing(self) -> None:
        """구분자 없이 산문만 오면 RuntimeError 를 발생시켜야 한다.

        조용히 빈 요약을 저장하면 Bedrock 비용은 나갔는데 사용자는 빈 결과를
        받고, 로그에도 원인이 남지 않는다.
        """
        with patch("app.services.summary_engine._get_bedrock_client") as mock_get:
            mock_client = MagicMock()
            mock_client.invoke_model.return_value = _mock_bedrock("이건 그냥 산문입니다")
            mock_get.return_value = mock_client

            with pytest.raises(RuntimeError, match="DETAILED 섹션이 없습니다"):
                await summarize_text("테스트 텍스트")

    @pytest.mark.asyncio
    async def test_summarize_raises_when_detailed_empty(self) -> None:
        """DETAILED 구분자만 있고 본문이 비어도 실패로 처리해야 한다."""
        response_text = "===GENRE===\nTECH\n===DETAILED===\n===INSIGHTS===\n- 하나\n"

        with patch("app.services.summary_engine._get_bedrock_client") as mock_get:
            mock_client = MagicMock()
            mock_client.invoke_model.return_value = _mock_bedrock(response_text)
            mock_get.return_value = mock_client

            with pytest.raises(RuntimeError, match="DETAILED 섹션이 없습니다"):
                await summarize_text("테스트 텍스트")
