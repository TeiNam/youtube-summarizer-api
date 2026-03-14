"""AWS 클라이언트 팩토리 모듈

.env 파일의 AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY를 사용하여
boto3 클라이언트를 생성한다. 모든 AWS 서비스 호출에서 이 모듈을 통해
클라이언트를 생성하여 자격 증명을 일관되게 관리한다.
"""

import os

import boto3

# AWS 자격 증명 (환경변수에서 로드)
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


def get_aws_client(service_name: str):
    """AWS boto3 클라이언트를 생성한다.

    환경변수에 access key/secret key가 설정되어 있으면
    명시적으로 자격 증명을 전달하고, 없으면 boto3 기본 체인을 사용한다.

    Args:
        service_name: AWS 서비스 이름 (예: "bedrock-runtime", "s3", "transcribe")

    Returns:
        boto3 클라이언트 인스턴스
    """
    kwargs = {"region_name": AWS_REGION}

    if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
        kwargs["aws_access_key_id"] = AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = AWS_SECRET_ACCESS_KEY

    return boto3.client(service_name, **kwargs)
