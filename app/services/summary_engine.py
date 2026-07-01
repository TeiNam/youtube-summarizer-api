"""요약 엔진 모듈

AWS Bedrock LLM(Claude 모델)을 활용하여 텍스트 번역과 요약을 수행한다.
번역: 원본 텍스트를 대상 언어로 변환
요약: 전체 요약문과 핵심 포인트 목록 생성

동기 I/O 호출(boto3)은 run_in_executor로 스레드풀에서 실행하여
이벤트 루프 블로킹을 방지한다.
"""

import asyncio
import json
import logging
import os
from functools import partial
from pathlib import Path

from app.services.aws_client import get_aws_client

logger = logging.getLogger(__name__)

# AWS Bedrock 설정 (환경변수에서 로드)
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
)

# 추론 강도(effort). Opus 4.8/4.6·Sonnet 4.6에서만 지원(Haiku·구형은 거부).
# 빈 값이면 본문에 넣지 않는다. 권장: 요약은 medium, 비용 절감은 low.
BEDROCK_EFFORT = os.environ.get("BEDROCK_EFFORT", "").strip()

# 프롬프트 템플릿 디렉터리 (PROMPTS_DIR 환경변수로 재정의 가능)
PROMPTS_DIR = Path(
    os.environ.get("PROMPTS_DIR", Path(__file__).resolve().parent.parent / "prompts")
)


def _build_body(prompt: str, max_tokens: int, *, use_effort: bool) -> str:
    """Bedrock invoke_model 요청 본문을 만든다.

    use_effort=True이고 BEDROCK_EFFORT가 설정돼 있으면 output_config.effort를
    추가한다. effort 미지원 모델(Haiku 등)에서는 환경변수를 비워 두면 된다.
    """
    payload: dict = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if use_effort and BEDROCK_EFFORT:
        payload["output_config"] = {"effort": BEDROCK_EFFORT}
    return json.dumps(payload)


def _render_prompt(name: str, **vars: str) -> str:
    """prompts/<name>.md 를 읽어 {{VAR}} 자리표시자를 치환한다.

    매 호출마다 디스크에서 읽으므로 재배포 없이 프롬프트를 튜닝할 수 있다.
    (파일 I/O는 Bedrock 호출 지연에 비해 무시할 수준)
    """
    template = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    for key, value in vars.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def _get_bedrock_client():
    """Bedrock Runtime 클라이언트를 생성한다."""
    return get_aws_client("bedrock-runtime")


def _invoke_bedrock_sync(body: str) -> dict:
    """Bedrock 모델을 동기적으로 호출한다 (스레드풀에서 실행용).

    Args:
        body: JSON 직렬화된 요청 본문

    Returns:
        Bedrock 응답 본문 딕셔너리
    """
    logger.info("Bedrock 호출 시작 (모델: %s)", BEDROCK_MODEL_ID)
    client = _get_bedrock_client()
    response = client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=body,
    )
    result = json.loads(response["body"].read())
    logger.info("Bedrock 호출 완료")
    return result


async def translate_text(text: str, target_language: str = "ko") -> str:
    """텍스트를 대상 언어로 번역한다.

    AWS Bedrock의 Claude 모델을 사용하여 번역을 수행한다.
    동기 I/O를 스레드풀에서 실행하여 이벤트 루프를 블로킹하지 않는다.

    Args:
        text: 번역할 원본 텍스트
        target_language: 대상 언어 코드 (기본값: "ko")

    Returns:
        번역된 텍스트

    Raises:
        RuntimeError: Bedrock 호출 실패 시
    """
    prompt = _render_prompt("translate", TARGET_LANGUAGE=target_language, TEXT=text)

    # 번역은 effort 불필요 (단순 변환) — 토큰 낭비 방지를 위해 미적용
    body = _build_body(prompt, max_tokens=20000, use_effort=False)

    try:
        loop = asyncio.get_running_loop()
        response_body = await loop.run_in_executor(
            None, partial(_invoke_bedrock_sync, body)
        )
        translated = response_body["content"][0]["text"]
        logger.info("번역 완료 (대상 언어: %s)", target_language)
        return translated

    except Exception as e:
        logger.error("번역 실패: %s", e, exc_info=True)
        raise RuntimeError(f"번역 실패: {e}") from e


async def summarize_text(text: str) -> dict:
    """텍스트를 장르별 전략으로 구조화된 요약을 생성한다.

    AWS Bedrock의 Claude 모델을 사용하여 장르 감지, 상세 요약,
    핵심 인사이트, 키워드를 포함한 풍부한 요약을 생성한다.

    Args:
        text: 요약할 텍스트 (번역된 자막)

    Returns:
        요약 결과 딕셔너리 (summary, key_points 키 포함)

    Raises:
        RuntimeError: Bedrock 호출 실패 시
    """
    prompt = _render_prompt("summarize", TEXT=text)

    # 요약은 심층 분석이므로 effort 적용 대상 (BEDROCK_EFFORT 설정 시)
    body = _build_body(prompt, max_tokens=20000, use_effort=True)

    try:
        loop = asyncio.get_running_loop()
        response_body = await loop.run_in_executor(
            None, partial(_invoke_bedrock_sync, body)
        )
        result_text = response_body["content"][0]["text"]

        # JSON 블록 추출 (```json ... ``` 형식 처리)
        if "```json" in result_text:
            json_str = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            json_str = result_text.split("```")[1].split("```")[0].strip()
        else:
            json_str = result_text.strip()

        result = json.loads(json_str)

        # 구조화된 요약 조합: 장르 + 한줄 요약 + 상세 요약 + 키워드 + 추가 탐색 주제
        genre = result.get("genre", "OTHER")
        one_line = result.get("one_line_summary", "")
        detailed = result.get("detailed_summary", "")
        keywords = result.get("keywords", [])
        further = result.get("further_topics", [])

        # summary 필드에 풍부한 마크다운 요약을 담는다
        summary_parts = [
            f"🏷️ 장르: {genre}",
            f"\n📌 한줄 요약\n{one_line}",
            f"\n📋 핵심 내용\n{detailed}",
        ]

        if keywords:
            kw_lines = "\n".join(
                f"- **{kw.get('term', '')}**: {kw.get('description', '')}"
                for kw in keywords
            )
            summary_parts.append(f"\n🔑 키워드 & 용어\n{kw_lines}")

        if further:
            ft_lines = "\n".join(f"- {t}" for t in further)
            summary_parts.append(f"\n❓ 추가 탐색 주제\n{ft_lines}")

        summary = "\n".join(summary_parts)

        # key_points에는 핵심 인사이트를 담는다
        key_points = result.get("key_insights", [])

        logger.info("요약 완료 (장르: %s)", genre)
        return {"summary": summary, "key_points": key_points}

    except json.JSONDecodeError as e:
        logger.error("요약 결과 JSON 파싱 실패: %s", e, exc_info=True)
        raise RuntimeError(f"요약 결과 파싱 실패: {e}") from e
    except Exception as e:
        logger.error("요약 실패: %s", e, exc_info=True)
        raise RuntimeError(f"요약 실패: {e}") from e
