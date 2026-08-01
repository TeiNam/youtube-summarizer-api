"""비ASCII 문자를 그대로 내보내는 JSON 응답 클래스

main.py 와 routes.py 가 각자 같은 클래스를 정의하고 있어 하나로 모았다.
"""

import json

from fastapi.responses import JSONResponse


class UnicodeJSONResponse(JSONResponse):
    """한글 등 비ASCII 문자를 이스케이프하지 않는 JSON 응답 클래스"""

    def render(self, content) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")
