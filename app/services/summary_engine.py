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

from app.services.aws_client import get_aws_client

logger = logging.getLogger(__name__)

# AWS Bedrock 설정 (환경변수에서 로드)
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
)


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
    client = _get_bedrock_client()
    response = client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=body,
    )
    return json.loads(response["body"].read())


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
    prompt = (
        f"다음 텍스트를 {target_language} 언어로 번역해주세요. "
        "번역된 텍스트만 출력하고, 다른 설명은 포함하지 마세요.\n\n"
        f"{text}"
    )

    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
    )

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
    prompt = (
        "# 역할\n"
        "당신은 유튜브 영상 콘텐츠 분석 전문 AI입니다.\n"
        "영상 자막을 입력받아 핵심 내용을 빠짐없이 추출하고, 구조화된 요약을 제공합니다.\n\n"
        "---\n\n"
        "# 처리 규칙\n\n"
        "## 1단계: 장르 자동 감지\n"
        "자막 내용을 분석하여 아래 장르 중 하나를 판별하세요:\n"
        "- NEWS: 뉴스, 시사, 정치, 사회 이슈\n"
        "- LECTURE: 강의, 교육, 다큐멘터리, 지식 전달\n"
        "- TECH: 기술, 개발, 프로그래밍, IT 리뷰\n"
        "- BUSINESS: 비즈니스, 자기계발, 생산성, 마케팅\n"
        "- FINANCE: 주식, 투자, 경제, 재테크\n"
        "- OTHER: 위에 해당하지 않는 일반 콘텐츠\n\n"
        "## 2단계: 장르별 요약 전략\n\n"
        "### [NEWS] 뉴스/시사\n"
        "- 육하원칙(누가, 언제, 어디서, 무엇을, 왜, 어떻게) 기반 정리\n"
        "- 팩트와 의견을 명확히 분리\n"
        "- 향후 전망이나 예상 영향 정리\n\n"
        "### [LECTURE] 강의/교육\n"
        "- 핵심 개념과 정의를 빠짐없이 추출\n"
        "- 개념 간 관계와 논리적 흐름 보존\n"
        "- 강사가 강조한 포인트 별도 표시\n\n"
        "### [TECH] 기술/개발\n"
        "- 기술 스택, 도구, 라이브러리명 정확히 기록\n"
        "- 코드 로직이나 구현 단계를 순서대로 정리\n"
        "- 트러블슈팅 팁이나 주의사항 별도 정리\n\n"
        "### [BUSINESS] 비즈니스/자기계발\n"
        "- 핵심 프레임워크나 방법론 추출\n"
        "- 실행 가능한 액션 아이템 정리\n\n"
        "### [FINANCE] 주식/투자\n"
        "- 언급된 종목명, 지표, 수치를 정확히 기록\n"
        "- 매수/매도/관망 등 방향성 판단의 근거 정리\n"
        "- ⚠️ 투자 판단은 시청자 본인 책임임을 명시\n\n"
        "---\n\n"
        "# 출력 형식\n\n"
        "반드시 아래 JSON 형식으로만 응답하세요.\n\n"
        "```json\n"
        "{\n"
        '  "genre": "감지된 장르 (NEWS/LECTURE/TECH/BUSINESS/FINANCE/OTHER)",\n'
        '  "one_line_summary": "영상 전체를 한 문장으로 압축",\n'
        '  "detailed_summary": "영상의 주요 내용을 시간 순서 또는 논리 순서로 정리한 상세 요약. '
        "각 포인트는 충분히 구체적으로 서술하고, 중요한 수치/이름/용어를 반드시 포함. "
        '마크다운 형식으로 작성.",\n'
        '  "key_insights": ["가장 가치 있는 정보 3~5개. 단순 요약이 아니라 이것만은 기억하라는 관점에서 선별"],\n'
        '  "keywords": [{"term": "키워드", "description": "간략한 설명"}],\n'
        '  "further_topics": ["더 깊이 이해하려면 찾아볼 만한 관련 주제 2~3개"]\n'
        "}\n"
        "```\n\n"
        "# 품질 원칙\n"
        "1. 빠짐없이: 영상의 주요 논점을 하나도 빠뜨리지 않는다\n"
        "2. 정확하게: 자막에 실제로 있는 내용만 정리한다 — 추측이나 외부 지식 추가 금지\n"
        "3. 구조적으로: 읽기 쉽게 논리적 순서로 배치한다\n"
        "4. 구체적으로: 모호한 표현 대신 실제 내용을 적는다\n"
        "5. 비례적으로: 텍스트 길이에 비례하여 요약 분량을 조절한다\n\n"
        "# 자막 품질 대응\n"
        "- 자동 생성 자막의 경우 고유명사나 전문 용어가 잘못 표기되었을 수 있으므로 문맥에 맞게 보정\n"
        "- 자막이 불완전한 구간은 [자막 불명확]으로 표시\n"
        "- 절대로 자막에 없는 내용을 임의로 채워 넣지 않는다\n\n"
        "---\n\n"
        f"[자막 원문]\n{text}"
    )

    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": prompt}],
        }
    )

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
