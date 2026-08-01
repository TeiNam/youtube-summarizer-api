# YouTube Summary API

![Python](https://img.shields.io/badge/Python-3.12-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.135-009688.svg)
![AWS](https://img.shields.io/badge/AWS-Cloud-FF9900.svg)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://qr.kakaopay.com/Ej74xpc815dc06149)

유튜브 영상 URL을 입력받아 자막 추출 → 번역 → 구조화된 요약을 수행하는 REST API입니다.

## 주요 기능

- 유튜브 자막 자동 추출 (자막 없는 영상은 AWS Transcribe로 음성 인식 폴백)
- 장르 자동 감지 (NEWS/LECTURE/TECH/BUSINESS/FINANCE/OTHER)
- 장르별 맞춤 요약 전략 적용
- AWS Bedrock (Claude) 기반 번역 및 요약
- API 키 인증 (X-API-Key 헤더)
- 비동기 작업 처리 (작업 ID로 상태 조회)
- AWS 호출은 IAM Roles Anywhere 임시 자격증명 (장기 액세스 키 없음)

## 기술 스택

- Python 3.12 / FastAPI / Uvicorn
- AWS Bedrock (Claude), S3, Transcribe
- AWS IAM Roles Anywhere (`aws_signing_helper` + `credential_process`)
- yt-dlp, youtube-transcript-api
- Docker

## 설치 및 실행

### 1. 로컬 실행

```bash
# 저장소 클론
git clone <repository-url>
cd youtube-summary-api

# 가상환경 생성 및 활성화
python -m venv .venv
source .venv/bin/activate

# 의존성 설치
pip install -r requirements.txt

# 환경변수 설정
cp .env.example .env
# .env 파일을 편집하여 AWS_PROFILE·API 키를 입력 (액세스 키는 쓰지 않는다)

# 서버 실행
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

로컬에서는 `~/.aws/config` 에 `wl-bedrock-test` 프로파일이 있어야 합니다
(자격증명 설정은 [AWS 자격증명](#aws-자격증명-iam-roles-anywhere) 참고).

### 2. Docker 실행 (로컬 빌드)

인증서 마운트 때문에 `docker run --env-file` 만으로는 자격증명이 붙지 않습니다. compose 를 씁니다.

```bash
# HOST_STATE_DIR 아래에 aws-ra/(인증서·키) 와 aws-config/config 를 미리 놓는다
cd docker && HOST_STATE_DIR=/path/to/state docker compose up -d --build
```

`HOST_STATE_DIR` 은 필수입니다. 비워 두면 compose 가 빈 디렉터리를 마운트하고
**자격증명이 조용히 실패**합니다(에러 대신 인증 오류만 로그에 남습니다).

### 3. NAS 배포 (GHCR 이미지)

```bash
cd /volume1/docker/youtube-summarizer
HOST_STATE_DIR=$PWD docker compose -f docker-compose.nas.yml up -d
```

`docker-compose.nas.yml` 은 `ghcr.io/teinam/youtube-summarizer-api:latest` 를 pull 하고
호스트 포트 8008 로 노출합니다. 환경변수는 `${ENV_FILE:-.env}` 에서 읽습니다
(Container Manager UI 에 박아 두지 않습니다 — 인증서 마운트가 필요해 compose 로 옮겼습니다).

## AWS 자격증명 (IAM Roles Anywhere)

장기 액세스 키를 쓰지 않습니다. `aws_signing_helper` 가 X.509 인증서로 임시 자격증명을
받고, SDK 가 만료 전에 다시 호출합니다. 앱 코드는 자격증명을 직접 다루지 않습니다 —
`get_aws_client()` 가 boto3 기본 체인에 맡기고, 체인이 `credential_process` 를 실행합니다.

역할·인증서·프로파일은 별도 저장소 [`aws-isengard-infra`](../aws-isengard-infra) 가
Terraform 으로 관리합니다. 이 앱이 쓰는 워크로드는 `wl-bedrock-test` 입니다.

### 필요한 것 세 가지

| 항목 | 로컬 | 컨테이너 |
|------|------|----------|
| helper 바이너리 | `~/.aws-ra/bin/aws_signing_helper` (infra 의 `bootstrap-client.sh`) | 이미지에 포함 (`/usr/local/bin/aws_signing_helper`, 버전·SHA256 고정) |
| 인증서·개인키 | `~/.aws-ra/primary/wl-bedrock-test.{crt,key}` | `${HOST_STATE_DIR}/aws-ra` → `/app/.aws-ra:ro` |
| `credential_process` config | `~/.aws/config` | `${HOST_STATE_DIR}/aws-config` → `/app/.aws:ro` + `AWS_CONFIG_FILE=/app/.aws/config` |

`credential_process` 는 셸을 거치지 않아 `~`·`$HOME` 이 확장되지 않고 PATH 탐색도 안 됩니다.
경로는 반드시 절대 경로여야 합니다. 컨테이너용 config 는 infra 저장소에서 렌더링합니다.

```bash
# aws-isengard-infra 에서 컨테이너 안의 경로로 config 를 뽑는다
./scripts/render-aws-config.sh /app/.aws-ra /usr/local/bin/aws_signing_helper > config
# 이 파일을 ${HOST_STATE_DIR}/aws-config/config 로 놓는다
```

### 인증서 배치 (호스트)

개인키는 600 이고 **컨테이너 실행 UID 소유**여야 합니다. UID 는 하드코딩하지 말고 읽어서 넣습니다.

```bash
UID_IN=$(docker exec youtube-summarizer-api id -u)   # 이 이미지는 appuser
STATE=/volume1/docker/youtube-summarizer
install -d -m 700 -o $UID_IN $STATE/aws-ra $STATE/aws-config
install -m 600 -o $UID_IN ~/.aws-ra/primary/wl-bedrock-test.key $STATE/aws-ra/
install -m 644 -o $UID_IN ~/.aws-ra/primary/wl-bedrock-test.crt $STATE/aws-ra/
```

### 자격증명 확인

```bash
AWS_PROFILE=wl-bedrock-test aws sts get-caller-identity   # assumed-role/... 이면 정상
docker exec youtube-summarizer-api aws sts get-caller-identity  # 컨테이너 안에서
```

`No such file or directory: /usr/local/bin/aws_signing_helper` 가 나오면 helper 가 이미지에
없는 것이고(구 이미지), 자격증명 오류만 나오면 인증서 마운트·퍼미션·시계(SigV4 5분 제한)를 봅니다.

## 환경변수 설정

`.env.example`을 참고하여 `.env` 파일을 작성합니다.

| 변수명 | 설명 | 필수 |
|--------|------|------|
| `AWS_PROFILE` | IAM Roles Anywhere 프로파일 (`wl-bedrock-test`) | O |
| `AWS_CONFIG_FILE` | `credential_process` 가 있는 config 경로. 컨테이너는 compose 가 `/app/.aws/config` 로 주입 | △ |
| `AWS_REGION` | AWS 리전 (예: `ap-northeast-2`) | O |
| `API_KEY` | API 인증키 (미설정 시 인증 비활성화) | △ |
| `API_PREFIX` | 리버스 프록시 경로 프리픽스 (예: `/yts/api`) | △ |
| `ROOT_PATH` | Swagger UI 경로 보정용 root_path | △ |
| `BEDROCK_MODEL_ID` | Bedrock 모델 ID (요약 품질 향상 시 `anthropic.claude-opus-4-8` 권장) | O |
| `BEDROCK_EFFORT` | 추론 강도 `low\|medium\|high\|max` (Opus 4.8/4.6·Sonnet 4.6 전용, Haiku는 비워둘 것) | △ |
| `TRANSCRIBE_S3_BUCKET` | 음성 인식용 S3 버킷명 | △ |
| `HOST_STATE_DIR` | (compose 전용, `.env` 아님) 인증서 마운트 원본 디렉터리 | O |

`AWS_ACCESS_KEY_ID`·`AWS_SECRET_ACCESS_KEY` 는 더 이상 쓰지 않습니다. 값이 남아 있으면
`get_aws_client()` 가 그 키를 우선 사용해 프로파일이 무시되므로, 이관 후에는 지워야 합니다.

### 함정 두 가지

- **`API_PREFIX` 와 `ROOT_PATH` 를 동시에 설정하면** 프리픽스가 두 번 적용되어 미들웨어가
  보는 경로가 `/yts/api/yts/api/...` 가 됩니다. 공개 경로 판정이 깨져 헬스체크가 401 을
  받습니다. 프록시가 프리픽스를 벗기지 않는 구성이면 `API_PREFIX` 만 씁니다.
- **`TRANSCRIBE_S3_BUCKET` 을 비우면** 코드 기본값 `youtube-summary-audio` 가 쓰이는데 그
  버킷은 계정에 없습니다(`NoSuchBucket`). 자막 없는 영상에서만 조용히 실패하므로 놓치기
  쉽습니다. IAM 정책에 포함된 버킷명을 반드시 넣습니다.

## API 사용법

### 영상 요약 요청

```bash
curl -X POST http://localhost:8000/summarize \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"url": "https://www.youtube.com/watch?v=VIDEO_ID"}'
```

응답:
```json
{"task_id": "uuid", "status": "pending"}
```

### 작업 상태 조회

```bash
curl http://localhost:8000/tasks/{task_id} \
  -H "X-API-Key: your-api-key"
```

### 테스트 실행

```bash
python -m pytest app/tests/ -v
```
