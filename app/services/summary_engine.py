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
import re
from functools import partial
from pathlib import Path

from app.services.aws_client import get_aws_client

logger = logging.getLogger(__name__)

# 프롬프트 템플릿의 {{VAR}} 자리표시자
_PLACEHOLDER_PATTERN = re.compile(r"\{\{([A-Z_][A-Z0-9_]*)\}\}")

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

# 모델에 보낼 텍스트 길이 상한(문자). 넘으면 잘라서 보낸다.
# 상한이 없으면 3시간 영상 자막이 컨텍스트 한도를 넘겨 호출 자체가 실패하고,
# 비용도 입력 길이에 선형으로 늘어난다. 기본값은 Claude 200k 컨텍스트를
# 한/영 혼합 기준으로 넉넉히 밑도는 값이다.
MAX_INPUT_CHARS = int(os.environ.get("BEDROCK_MAX_INPUT_CHARS", "200000"))


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


def _truncate(text: str, limit: int | None = None) -> str:
    """모델 입력 길이를 상한으로 자른다 (잘렸으면 로그를 남긴다).

    상한은 호출 시점에 읽는다 — 기본 인자로 묶으면 정의 시점 값이 박혀
    설정을 바꿔도 반영되지 않는다.
    """
    if limit is None:
        limit = MAX_INPUT_CHARS
    if len(text) <= limit:
        return text
    logger.warning(
        "입력이 상한을 넘어 잘랐습니다: %d자 → %d자 (뒷부분 유실)", len(text), limit
    )
    return text[:limit]


def _render_prompt(name: str, **vars: str) -> str:
    """prompts/<name>.md 를 읽어 {{VAR}} 자리표시자를 치환한다.

    매 호출마다 디스크에서 읽으므로 재배포 없이 프롬프트를 튜닝할 수 있다.
    (파일 I/O는 Bedrock 호출 지연에 비해 무시할 수준)

    치환은 템플릿을 한 번만 훑는다 — 순차 replace 를 쓰면 먼저 치환된 값 안의
    '{{...}}' 문자열이 다음 치환 대상이 되어, 자막 내용이 자리표시자를 흉내내
    프롬프트를 조작할 수 있다.
    """
    template = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")

    def _substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        # 정의되지 않은 자리표시자는 원문 그대로 남긴다
        return vars.get(key, match.group(0))

    return _PLACEHOLDER_PATTERN.sub(_substitute, template)


def _extract_text(content: list[dict]) -> str:
    """content 블록 목록에서 첫 번째 text 블록을 찾아 반환한다.

    Sonnet 5+ 모델은 기본적으로 thinking이 켜져 있어 content[0]이
    thinking 블록일 수 있으므로 인덱스 고정 대신 type으로 찾는다.
    type 키가 없는 응답(구형 모델·모킹)은 text 키 유무로 판정한다.
    """
    for block in content:
        if block.get("type") == "text" or ("type" not in block and "text" in block):
            return block["text"]
    raise ValueError(f"text 블록을 찾을 수 없음: {content}")


def _check_not_truncated(response_body: dict, what: str) -> None:
    """응답이 max_tokens 로 잘렸는지 확인한다.

    잘린 응답을 그대로 성공 처리하면 번역문 뒷부분이 사라진 채 완료되고,
    요약에서는 JSON 이 닫히지 않아 파싱 실패로 나타난다. 잘렸다는 사실을
    드러내는 편이 낫다.

    Raises:
        RuntimeError: stop_reason 이 max_tokens 인 경우
    """
    if response_body.get("stop_reason") == "max_tokens":
        usage = response_body.get("usage", {})
        raise RuntimeError(
            f"{what} 결과가 max_tokens 로 잘렸습니다 "
            f"(출력 {usage.get('output_tokens', '?')} 토큰). "
            "입력이 너무 길거나 max_tokens 가 작습니다."
        )


def _coerce_str_list(value: object) -> list[str]:
    """LLM 이 준 값을 문자열 리스트로 정규화한다.

    프롬프트는 문자열 배열을 요구하지만 모델은 이를 어길 수 있다 —
    null, 단일 문자열, 객체 배열이 실제로 온다. 그대로 저장하면 요약은
    성공했는데 조회 시점에 응답 모델 검증이 터져 **완료된 작업이 영구히
    500** 이 된다(실측). 그래서 저장 전에 여기서 흡수한다.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return [str(value)]

    items: list[str] = []
    for item in value:
        if item is None:
            continue
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            # {"point": "..."} / {"insight": "..."} 처럼 감싸서 오는 경우
            text = next(
                (str(v) for v in item.values() if isinstance(v, str) and v.strip()),
                json.dumps(item, ensure_ascii=False),
            )
        else:
            text = str(item)
        if text.strip():
            items.append(text)
    return items


def _coerce_str(value: object) -> str:
    """LLM 이 준 값을 문자열로 정규화한다 (None → 빈 문자열)."""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


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
    prompt = _render_prompt(
        "translate", TARGET_LANGUAGE=target_language, TEXT=_truncate(text)
    )

    # 번역은 effort 불필요 (단순 변환) — 토큰 낭비 방지를 위해 미적용
    body = _build_body(prompt, max_tokens=20000, use_effort=False)

    try:
        loop = asyncio.get_running_loop()
        response_body = await loop.run_in_executor(
            None, partial(_invoke_bedrock_sync, body)
        )
        _check_not_truncated(response_body, "번역")
        translated = _extract_text(response_body["content"])
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
    prompt = _render_prompt("summarize", TEXT=_truncate(text))

    # 요약은 심층 분석이므로 effort 적용 대상 (BEDROCK_EFFORT 설정 시)
    body = _build_body(prompt, max_tokens=20000, use_effort=True)

    try:
        loop = asyncio.get_running_loop()
        response_body = await loop.run_in_executor(
            None, partial(_invoke_bedrock_sync, body)
        )
        _check_not_truncated(response_body, "요약")
        result_text = _extract_text(response_body["content"])

        # JSON 블록 추출 (```json ... ``` 형식 처리)
        if "```json" in result_text:
            json_str = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            json_str = result_text.split("```")[1].split("```")[0].strip()
        else:
            json_str = result_text.strip()

        result = json.loads(json_str)

        # 모델이 JSON 배열이나 문자열을 최상위로 줄 수도 있다 — dict 가 아니면 거부
        if not isinstance(result, dict):
            raise RuntimeError(
                f"요약 결과가 JSON 객체가 아닙니다: {type(result).__name__}"
            )

        # 구조화된 요약 조합: 장르 + 한줄 요약 + 상세 요약 + 키워드 + 추가 탐색 주제
        # 모든 필드를 정규화한다 — 모델이 프롬프트의 형식을 어겨도 저장 후
        # 조회에서 터지지 않게 한다(잘못된 형태를 조회 시점까지 미루지 않는다).
        genre = _coerce_str(result.get("genre")) or "OTHER"
        one_line = _coerce_str(result.get("one_line_summary"))
        detailed = _coerce_str(result.get("detailed_summary"))
        keywords = result.get("keywords")
        further = _coerce_str_list(result.get("further_topics"))

        # summary 필드에 풍부한 마크다운 요약을 담는다
        summary_parts = [
            f"🏷️ 장르: {genre}",
            f"\n📌 한줄 요약\n{one_line}",
            f"\n📋 핵심 내용\n{detailed}",
        ]

        if isinstance(keywords, list) and keywords:
            kw_lines = "\n".join(
                f"- **{_coerce_str(kw.get('term'))}**: {_coerce_str(kw.get('description'))}"
                if isinstance(kw, dict)
                else f"- {_coerce_str(kw)}"
                for kw in keywords
            )
            summary_parts.append(f"\n🔑 키워드 & 용어\n{kw_lines}")

        if further:
            ft_lines = "\n".join(f"- {t}" for t in further)
            summary_parts.append(f"\n❓ 추가 탐색 주제\n{ft_lines}")

        summary = "\n".join(summary_parts)

        # key_points에는 핵심 인사이트를 담는다 (응답 모델이 list[str] 을 요구한다)
        key_points = _coerce_str_list(result.get("key_insights"))

        logger.info("요약 완료 (장르: %s)", genre)
        return {"summary": summary, "key_points": key_points}

    except json.JSONDecodeError as e:
        logger.error("요약 결과 JSON 파싱 실패: %s", e, exc_info=True)
        raise RuntimeError(f"요약 결과 파싱 실패: {e}") from e
    except Exception as e:
        logger.error("요약 실패: %s", e, exc_info=True)
        raise RuntimeError(f"요약 실패: {e}") from e
