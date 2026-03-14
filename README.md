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

## 기술 스택

- Python 3.12 / FastAPI / Uvicorn
- AWS Bedrock (Claude), S3, Transcribe
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
# .env 파일을 편집하여 AWS 자격 증명과 API 키를 입력

# 서버 실행
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 2. Docker 실행

```bash
# 이미지 빌드
docker build -f docker/Dockerfile -t youtube-summary-api .

# 컨테이너 실행
docker run -d --env-file .env -p 8000:8000 youtube-summary-api

# 또는 docker compose 사용
cd docker && docker compose up -d
```

## 환경변수 설정

`.env.example`을 참고하여 `.env` 파일을 작성합니다.

| 변수명 | 설명 | 필수 |
|--------|------|------|
| `AWS_ACCESS_KEY_ID` | AWS 액세스 키 | O |
| `AWS_SECRET_ACCESS_KEY` | AWS 시크릿 키 | O |
| `AWS_REGION` | AWS 리전 (예: `ap-northeast-2`) | O |
| `API_KEY` | API 인증키 (미설정 시 인증 비활성화) | △ |
| `BEDROCK_MODEL_ID` | Bedrock 모델 ID | O |
| `TRANSCRIBE_S3_BUCKET` | 음성 인식용 S3 버킷명 | △ |

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
