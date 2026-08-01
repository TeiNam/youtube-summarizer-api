"""유튜브 URL 검증 및 비디오 ID 추출 모듈

정규식 search 로 문자열 어딘가의 패턴을 찾는 방식은 쓰지 않는다 — 그러면
`https://notyoutube.com/watch?v=...` 나 `javascript:alert(1)#...youtube.com/...`
같은 입력도 통과한다(실측). urlsplit 으로 스킴과 호스트를 실제로 파싱한다.
"""

from urllib.parse import parse_qs, urlsplit

# 비디오 ID 는 11자리 [A-Za-z0-9_-]
_VIDEO_ID_LENGTH = 11
_VIDEO_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)

# watch 형식을 받는 호스트 (www./m./music. 접두 허용)
_WATCH_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }
)
# 단축 URL 호스트
_SHORT_HOSTS = frozenset({"youtu.be", "www.youtu.be"})

# 경로에 ID 가 실리는 형식들: /embed/ID, /shorts/ID, /live/ID, /v/ID
_PATH_PREFIXES = ("/embed/", "/shorts/", "/live/", "/v/")


def _is_video_id(candidate: str) -> bool:
    """11자리 비디오 ID 형식인지 확인한다."""
    return len(candidate) == _VIDEO_ID_LENGTH and set(candidate) <= _VIDEO_ID_CHARS


def validate_youtube_url(url: str) -> str:
    """유튜브 URL을 검증하고 비디오 ID를 반환한다.

    스킴(http/https)과 호스트를 파싱해 실제 유튜브 도메인인지 확인한다.
    지원 형식: watch?v=ID, youtu.be/ID, /embed/ID, /shorts/ID, /live/ID, /v/ID.
    스킴이 없으면 https 로 간주한다(기존 동작 유지).

    Args:
        url: 검증할 유튜브 URL 문자열

    Returns:
        추출된 11자리 비디오 ID

    Raises:
        ValueError: URL이 비어있거나, 유튜브 도메인이 아니거나,
                    비디오 ID를 찾을 수 없는 경우
    """
    if not url or not url.strip():
        raise ValueError("URL이 비어있거나 공백입니다")

    raw = url.strip()

    # 스킴 없는 입력(youtube.com/watch?v=...)도 받아준다
    if "//" not in raw.split("?", 1)[0]:
        raw = f"https://{raw}"

    parts = urlsplit(raw)

    # http/https 만 허용 — javascript:, data:, file: 등을 배제한다
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"유효하지 않은 유튜브 URL입니다: {url}")

    host = (parts.hostname or "").lower()

    if host in _WATCH_HOSTS:
        # /watch?v=ID
        if parts.path == "/watch":
            video_ids = parse_qs(parts.query).get("v", [])
            if video_ids and _is_video_id(video_ids[0]):
                return video_ids[0]
        # /embed/ID, /shorts/ID, /live/ID, /v/ID
        for prefix in _PATH_PREFIXES:
            if parts.path.startswith(prefix):
                candidate = parts.path[len(prefix) :].split("/", 1)[0]
                if _is_video_id(candidate):
                    return candidate

    elif host in _SHORT_HOSTS:
        candidate = parts.path.lstrip("/").split("/", 1)[0]
        if _is_video_id(candidate):
            return candidate

    raise ValueError(f"유효하지 않은 유튜브 URL입니다: {url}")
