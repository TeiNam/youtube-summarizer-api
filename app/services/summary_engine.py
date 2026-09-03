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

# 요약 응답의 섹션 구분자 (`===DETAILED===` 처럼 줄 전체가 구분자여야 한다)
_SECTION_PATTERN = re.compile(r"^===[ \t]*([A-Z_]+)[ \t]*===[ \t]*$", re.MULTILINE)

# 목록 표식: `- `, `* `, `• `, `1. `, `1) `
# 번호는 두 자리까지만 본다 — `\d+` 로 열어 두면 "2026. 하반기에는~" 처럼 연도로
# 시작하는 인사이트의 연도를 표식으로 보고 잘라낸다(내용 유실).
_BULLET_PATTERN = re.compile(r"(?:[-*•]|\d{1,2}[.)])[ \t]+")

# 프롬프트가 허용하는 장르 (섹션 값이 자유 텍스트라 여기서 좁힌다)
_GENRES = ("NEWS", "LECTURE", "TECH", "BUSINESS", "FINANCE", "OTHER")

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

# 모델 응답 토큰 상한. effort/thinking 이 켜져 있으면 사고 토큰도 이 예산을
# 함께 소진하므로, 본문만 계산한 값으로는 긴 영상에서 응답이 잘린다.
# 기본값은 Claude 4.x/Opus 5 계열의 출력 한도(64k)이며, 출력 한도가 더 낮은
# 모델(Haiku 3 = 4096 등)을 쓸 때는 반드시 내려야 한다.
MAX_OUTPUT_TOKENS = int(os.environ.get("BEDROCK_MAX_OUTPUT_TOKENS", "64000"))


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

    잘린 응답을 그대로 성공 처리하면 번역문 뒷부분이 사라진 채 완료된다.
    요약에서는 이 검사가 유일한 방어선이다 — 섹션 형식은 뒤쪽 섹션(INSIGHTS
    등)이 통째로 없어도 파싱은 성공하므로, 인사이트가 빈 요약이 조용히 저장된다.
    (JSON 이던 시절엔 닫히지 않은 괄호가 대신 알려 줬다.)

    Raises:
        RuntimeError: stop_reason 이 max_tokens 인 경우
    """
    if response_body.get("stop_reason") == "max_tokens":
        usage = response_body.get("usage", {})
        raise RuntimeError(
            f"{what} 결과가 max_tokens 로 잘렸습니다 "
            f"(출력 {usage.get('output_tokens', '?')} / 상한 {MAX_OUTPUT_TOKENS} 토큰). "
            "BEDROCK_MAX_OUTPUT_TOKENS 를 올리거나 입력을 줄이세요."
        )


def _parse_sections(text: str) -> dict[str, str]:
    """`===NAME===` 구분자로 나눈 {섹션명: 본문} 을 만든다.

    JSON 을 쓰지 않는 이유: 4KB 넘는 마크다운을 JSON 문자열 하나에 담게 하면,
    모델이 따옴표 하나를 escape 하지 않는 순간 응답 전체가 버려진다 —
    Bedrock 호출은 이미 성공하고 과금까지 끝난 뒤다(실측: detailed_summary
    4.1KB 지점의 " 하나로 `Expecting ',' delimiter`, 작업 전체 실패).
    구분자 방식은 본문에 따옴표·역슬래시·개행·코드블록이 그대로 들어가도 깨지지 않는다.

    구분자는 줄 전체가 `===NAME===` 여야 한다 — 마크다운 setext 제목(`====`)이나
    본문 안의 `===` 와 겹치지 않는다. 같은 섹션이 두 번 오면 뒤쪽을 쓴다
    (모델이 형식 예시를 먼저 되풀이하는 경우가 있다).
    """
    parts = _SECTION_PATTERN.split(text)
    # split 결과는 [머리말, 이름1, 본문1, 이름2, 본문2, ...] 형태다
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def _parse_bullets(block: str) -> list[str]:
    """목록 블록을 문자열 리스트로 만든다.

    응답 모델이 list[str] 을 요구하므로 여기서 형태를 확정한다. 모델이 목록
    표식을 빼거나 번호로 쓰거나 한 항목을 여러 줄로 쓰는 경우를 모두 흡수한다 —
    표식이 있으면 표식이 경계, 없으면 빈 줄이 경계다.
    """
    items: list[str] = []
    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            items.append("")  # 빈 줄은 항목 경계 (마지막에 걸러낸다)
            continue
        marker = _BULLET_PATTERN.match(line)
        if marker:
            items.append(line[marker.end() :].strip())
        elif items and items[-1]:
            items[-1] = f"{items[-1]} {line}"  # 표식 없는 줄은 앞 항목의 이어지는 문장
        else:
            items.append(line)
    return [item for item in items if item]


def _normalize_genre(block: str) -> str:
    """GENRE 섹션에서 허용된 장르만 뽑는다 (없으면 OTHER).

    섹션은 자유 텍스트라 모델이 "LECTURE (강의)" 처럼 적을 수 있다. 그대로 쓰면
    사용자 요약 머리에 그 문장이 박히므로 허용 목록으로 좁힌다.
    """
    upper = block.upper()
    return next((genre for genre in _GENRES if genre in upper), "OTHER")


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
    body = _build_body(prompt, max_tokens=MAX_OUTPUT_TOKENS, use_effort=False)

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
    body = _build_body(prompt, max_tokens=MAX_OUTPUT_TOKENS, use_effort=True)

    try:
        loop = asyncio.get_running_loop()
        response_body = await loop.run_in_executor(
            None, partial(_invoke_bedrock_sync, body)
        )
        _check_not_truncated(response_body, "요약")
        result_text = _extract_text(response_body["content"])

        sections = _parse_sections(result_text)

        detailed = sections.get("DETAILED", "")
        if not detailed:
            # 구분자를 못 찾으면 형식 위반이다. 조용히 빈 요약을 저장하면 호출
            # 비용은 나갔는데 사용자는 빈 결과를 받고, 원인도 남지 않는다.
            raise RuntimeError(
                "요약 결과에 DETAILED 섹션이 없습니다 "
                f"(받은 섹션: {', '.join(sorted(sections)) or '없음'}, "
                f"응답 앞부분: {result_text[:200]!r})"
            )

        genre = _normalize_genre(sections.get("GENRE", ""))
        one_line = sections.get("ONE_LINE", "")
        keywords = sections.get("KEYWORDS", "")
        further = sections.get("FURTHER", "")

        # summary 필드에 풍부한 마크다운 요약을 담는다.
        # KEYWORDS·FURTHER 는 모델이 이미 마크다운 목록으로 주므로 그대로 붙인다.
        summary_parts = [
            f"🏷️ 장르: {genre}",
            f"\n📌 한줄 요약\n{one_line}",
            f"\n📋 핵심 내용\n{detailed}",
        ]
        if keywords:
            summary_parts.append(f"\n🔑 키워드 & 용어\n{keywords}")
        if further:
            summary_parts.append(f"\n❓ 추가 탐색 주제\n{further}")

        summary = "\n".join(summary_parts)

        # key_points에는 핵심 인사이트를 담는다 (응답 모델이 list[str] 을 요구한다)
        key_points = _parse_bullets(sections.get("INSIGHTS", ""))

        logger.info("요약 완료 (장르: %s, 인사이트 %d개)", genre, len(key_points))
        return {"summary": summary, "key_points": key_points}

    except Exception as e:
        logger.error("요약 실패: %s", e, exc_info=True)
        raise RuntimeError(f"요약 실패: {e}") from e
