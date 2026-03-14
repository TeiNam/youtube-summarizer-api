"""테스트 공통 설정

테스트 환경에서 API_KEY를 설정하여 인증 미들웨어를 통과하도록 한다.
API_PREFIX가 설정된 경우 테스트 경로에 반영한다.
"""

import os

import pytest

from app.main import API_PREFIX

# 테스트용 API 키 (모든 테스트 세션에서 동일하게 사용)
TEST_API_KEY = "test-api-key-for-testing"

# API 경로 프리픽스 (환경변수에 따라 동적 적용)
PREFIX = API_PREFIX


@pytest.fixture(autouse=True)
def set_test_api_key(monkeypatch):
    """모든 테스트에서 API_KEY 환경변수를 설정한다.

    autouse=True로 모든 테스트에 자동 적용된다.
    app.main 모듈의 API_KEY 변수도 함께 패치한다.
    """
    monkeypatch.setenv("API_KEY", TEST_API_KEY)
    monkeypatch.setattr("app.main.API_KEY", TEST_API_KEY)
