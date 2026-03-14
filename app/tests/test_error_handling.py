"""오류 처리 및 로깅 테스트 모듈

글로벌 예외 핸들러와 요청/응답 로깅 미들웨어의 동작을 검증한다.
- 500 상태 코드 검증 (예상치 못한 예외 발생 시)
- 504 상태 코드 검증 (타임아웃 예외 발생 시)
- 오류 응답 형식 검증 (ErrorResponse 형식)
- 로깅 검증
"""

import logging
from unittest.mock import patch

from botocore.exceptions import ConnectTimeoutError, ReadTimeoutError
from fastapi.testclient import TestClient

from app.main import app
from app.tests.conftest import PREFIX


# 테스트 클라이언트
AUTH_HEADERS = {"X-API-Key": "test-api-key-for-testing"}
client = TestClient(app, raise_server_exceptions=False)


# =============================================================================
# 500 내부 서버 오류 핸들러 테스트
# =============================================================================


class TestInternalServerError:
    """예상치 못한 예외 발생 시 500 응답 검증"""

    def test_unexpected_exception_returns_500(self) -> None:
        """처리되지 않은 예외 발생 시 500 상태 코드를 반환해야 한다."""
        with patch(
            "app.api.routes.validate_youtube_url",
            side_effect=RuntimeError("예상치 못한 오류"),
        ):
            response = client.post(
                f"{PREFIX}/summarize",
                json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                headers=AUTH_HEADERS,
            )
        assert response.status_code == 500

    def test_unexpected_exception_returns_error_response_format(self) -> None:
        """500 오류 응답이 ErrorResponse 형식이어야 한다."""
        with patch(
            "app.api.routes.validate_youtube_url",
            side_effect=RuntimeError("테스트 오류"),
        ):
            response = client.post(
                f"{PREFIX}/summarize",
                json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                headers=AUTH_HEADERS,
            )
        data = response.json()
        # ErrorResponse 형식 검증: error.code, error.message 필드 존재
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert data["error"]["code"] == "INTERNAL_ERROR"

    def test_unexpected_exception_logs_stack_trace(self, caplog) -> None:
        """예상치 못한 예외 발생 시 스택 트레이스가 로깅되어야 한다."""
        with caplog.at_level(logging.ERROR):
            with patch(
                "app.api.routes.validate_youtube_url",
                side_effect=RuntimeError("스택 트레이스 테스트"),
            ):
                client.post(
                    f"{PREFIX}/summarize",
                    json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                    headers=AUTH_HEADERS,
                )
        # 오류 로그에 예외 메시지가 포함되어야 한다
        assert any("스택 트레이스 테스트" in record.message for record in caplog.records)


# =============================================================================
# 504 타임아웃 핸들러 테스트
# =============================================================================


class TestTimeoutError:
    """AWS 서비스 타임아웃 시 504 응답 검증"""

    def test_read_timeout_returns_504(self) -> None:
        """ReadTimeoutError 발생 시 504 상태 코드를 반환해야 한다."""
        with patch(
            "app.api.routes.validate_youtube_url",
            side_effect=ReadTimeoutError(endpoint_url="https://bedrock.amazonaws.com"),
        ):
            response = client.post(
                f"{PREFIX}/summarize",
                json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                headers=AUTH_HEADERS,
            )
        assert response.status_code == 504

    def test_connect_timeout_returns_504(self) -> None:
        """ConnectTimeoutError 발생 시 504 상태 코드를 반환해야 한다."""
        with patch(
            "app.api.routes.validate_youtube_url",
            side_effect=ConnectTimeoutError(endpoint_url="https://transcribe.amazonaws.com"),
        ):
            response = client.post(
                f"{PREFIX}/summarize",
                json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                headers=AUTH_HEADERS,
            )
        assert response.status_code == 504

    def test_timeout_returns_error_response_format(self) -> None:
        """타임아웃 오류 응답이 ErrorResponse 형식이어야 한다."""
        with patch(
            "app.api.routes.validate_youtube_url",
            side_effect=ReadTimeoutError(endpoint_url="https://bedrock.amazonaws.com"),
        ):
            response = client.post(
                f"{PREFIX}/summarize",
                json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                headers=AUTH_HEADERS,
            )
        data = response.json()
        # ErrorResponse 형식 검증
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert data["error"]["code"] == "SERVICE_TIMEOUT"

    def test_timeout_logs_error(self, caplog) -> None:
        """타임아웃 발생 시 오류가 로깅되어야 한다."""
        with caplog.at_level(logging.ERROR):
            with patch(
                "app.api.routes.validate_youtube_url",
                side_effect=ReadTimeoutError(endpoint_url="https://bedrock.amazonaws.com"),
            ):
                client.post(
                    f"{PREFIX}/summarize",
                    json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                    headers=AUTH_HEADERS,
                )
        # 타임아웃 관련 로그가 기록되어야 한다
        assert any("타임아웃" in record.message for record in caplog.records)


# =============================================================================
# 요청/응답 로깅 미들웨어 테스트
# =============================================================================


class TestRequestResponseLogging:
    """요청/응답 로깅 미들웨어 검증"""

    def test_successful_request_is_logged(self, caplog) -> None:
        """성공적인 요청이 로깅되어야 한다."""
        with caplog.at_level(logging.INFO):
            client.get(f"{PREFIX}/tasks/some-task-id", headers=AUTH_HEADERS)
        # HTTP 메서드와 경로가 로그에 포함되어야 한다
        assert any(
            "GET" in record.message and "/tasks/" in record.message
            for record in caplog.records
        )

    def test_log_contains_status_code(self, caplog) -> None:
        """로그에 HTTP 상태 코드가 포함되어야 한다."""
        with caplog.at_level(logging.INFO):
            client.get(f"{PREFIX}/tasks/nonexistent-id", headers=AUTH_HEADERS)
        # 404 상태 코드가 로그에 포함되어야 한다
        assert any("404" in record.message for record in caplog.records)

    def test_log_contains_process_time(self, caplog) -> None:
        """로그에 처리 시간이 포함되어야 한다."""
        with caplog.at_level(logging.INFO):
            client.get(f"{PREFIX}/tasks/some-id", headers=AUTH_HEADERS)
        # 처리 시간(ms) 표시가 로그에 포함되어야 한다
        assert any("ms" in record.message for record in caplog.records)
