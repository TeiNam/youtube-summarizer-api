"""AWS 클라이언트 팩토리 모듈

모든 AWS 서비스 호출은 이 모듈을 통해 클라이언트를 생성한다.

자격증명은 IAM Roles Anywhere 를 쓴다 — AWS_PROFILE 만 주면 boto3 기본 체인이
~/.aws/config(컨테이너는 AWS_CONFIG_FILE)의 credential_process 를 실행해 임시
자격증명을 받는다. 이 모듈은 자격증명을 직접 다루지 않는다.

AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY 분기는 롤백용으로만 남겨 둔다. 값이 있으면
그 키가 프로파일보다 우선하므로 이관 후에는 환경에서 지워야 한다.
"""

import os

import boto3
from botocore.config import Config

# 레거시 액세스 키 (이관 후에는 비어 있다 — 롤백 경로로만 남겨 둔다)
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Bedrock은 긴 텍스트 처리 시 응답이 느릴 수 있으므로 타임아웃을 넉넉히 설정
_BEDROCK_CONFIG = Config(
    read_timeout=300,      # 읽기 타임아웃 5분
    connect_timeout=10,    # 연결 타임아웃 10초
    retries={"max_attempts": 2},
)


def get_aws_client(service_name: str):
    """AWS boto3 클라이언트를 생성한다.

    기본은 boto3 기본 체인이다(= AWS_PROFILE 의 credential_process → 임시 자격증명).
    레거시 액세스 키가 환경에 남아 있으면 그것을 명시 전달한다(롤백 경로).
    bedrock-runtime 서비스는 별도 타임아웃 설정을 적용한다.

    Args:
        service_name: AWS 서비스 이름 (예: "bedrock-runtime", "s3", "transcribe")

    Returns:
        boto3 클라이언트 인스턴스
    """
    kwargs = {"region_name": AWS_REGION}

    if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
        kwargs["aws_access_key_id"] = AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = AWS_SECRET_ACCESS_KEY

    # Bedrock 서비스에는 긴 타임아웃 설정 적용
    if service_name == "bedrock-runtime":
        kwargs["config"] = _BEDROCK_CONFIG

    return boto3.client(service_name, **kwargs)
