# 구현 태스크

## 태스크 1: 프로젝트 초기 설정

- [x] 1.1 프로젝트 디렉토리 구조 생성 (`app/`, `app/api/`, `app/models/`, `app/services/`, `app/tests/`)
- [x] 1.2 `requirements.txt` 생성 (fastapi, uvicorn, youtube-transcript-api, yt-dlp, boto3, hypothesis, pytest, httpx, moto, pydantic)
- [x] 1.3 `app/main.py` 생성 - FastAPI 앱 인스턴스, 글로벌 예외 핸들러, 로깅 설정
- [x] 1 프로젝트 초기 설정

## 태스크 2: 데이터 모델 정의

- [x] 2.1 `app/models/requests.py` 생성 - `SummarizeRequest` 모델 (url, target_language 필드)
- [x] 2.2 `app/models/responses.py` 생성 - `TaskStatus` 열거형, `SummaryResult`, `TaskResponse`, `TaskDetailResponse`, `ErrorDetail`, `ErrorResponse` 모델
- [x] 2 데이터 모델 정의

## 태스크 3: URL 검증기 구현

- [x] 3.1 `app/services/url_validator.py` 생성 - `validate_youtube_url()` 함수 구현 (정규식 기반 URL 패턴 매칭, 비디오 ID 추출)
- [x] 3.2 `app/tests/test_url_validator.py` 생성 - Property 1 (유효한 URL에서 비디오 ID 추출) 속성 테스트 구현
  - [x] 3.2.1 Property 1: 임의의 비디오 ID로 두 URL 형식에서 동일한 ID 추출 검증 `[PBT: Property 1]`
- [x] 3.3 `app/tests/test_url_validator.py`에 Property 2 (유효하지 않은 URL 거부) 속성 테스트 추가
  - [x] 3.3.1 Property 2: 유튜브 패턴에 매칭되지 않는 임의 문자열에 대해 ValueError 발생 검증 `[PBT: Property 2]`
- [x] 3.4 `app/tests/test_url_validator.py`에 엣지 케이스 단위 테스트 추가 (빈 문자열, 공백 문자열)
- [x] 3 URL 검증기 구현

## 태스크 4: 자막 추출기 구현

- [x] 4.1 `app/services/subtitle_extractor.py` 생성 - `extract_subtitles()` 함수 구현 (youtube-transcript-api 활용, 원본 언어 우선 선택 로직)
- [x] 4.2 `app/tests/test_subtitle_extractor.py` 생성 - Property 3 (원본 언어 자막 우선 선택) 속성 테스트 구현
  - [x] 4.2.1 Property 3: 임의의 언어 목록에서 원본 언어가 포함되면 항상 원본 언어 선택 검증 `[PBT: Property 3]`
- [x] 4.3 `app/tests/test_subtitle_extractor.py`에 단위 테스트 추가 (자막 존재/부재, 추출 오류 시 None 반환)
- [x] 4 자막 추출기 구현

## 태스크 5: 음성 인식기 구현

- [x] 5.1 `app/services/audio_transcriber.py` 생성 - `transcribe_audio()` 함수 구현 (yt-dlp 오디오 다운로드, S3 업로드, AWS Transcribe 호출)
- [x] 5.2 `app/tests/test_audio_transcriber.py` 생성 - 단위 테스트 (오디오 다운로드 실패, Transcribe 실패, 성공 시나리오 모킹)
- [x] 5 음성 인식기 구현

## 태스크 6: 요약 엔진 구현

- [x] 6.1 `app/services/summary_engine.py` 생성 - `translate_text()`, `summarize_text()` 함수 구현 (AWS Bedrock invoke_model 호출)
- [x] 6.2 `app/tests/test_summary_engine.py` 생성 - 단위 테스트 (번역/요약 성공, Bedrock 호출 실패 시나리오 모킹)
- [x] 6 요약 엔진 구현

## 태스크 7: 작업 관리자 구현

- [x] 7.1 `app/services/task_manager.py` 생성 - `TaskManager` 클래스 구현 (create_task, get_task, update_status)
- [x] 7.2 `app/tests/test_task_manager.py` 생성 - Property 6 (작업 상태 조회 정확성) 속성 테스트 구현
  - [x] 7.2.1 Property 6: 임의의 상태 업데이트 후 조회 시 정확한 상태 반환 검증 `[PBT: Property 6]`
- [x] 7.3 `app/tests/test_task_manager.py`에 Property 7 (미등록 작업 ID 조회 시 None 반환) 속성 테스트 추가
  - [x] 7.3.1 Property 7: 임의의 UUID로 미등록 작업 조회 시 None 반환 검증 `[PBT: Property 7]`
- [x] 7 작업 관리자 구현

## 태스크 8: 처리 파이프라인 구현

- [x] 8.1 `app/services/pipeline.py` 생성 - `process_summary()` 함수 구현 (자막 추출 → 폴백 음성 인식 → 번역 → 요약, 단계별 상태 업데이트)
- [x] 8.2 `app/tests/test_pipeline.py` 생성 - 통합 단위 테스트 (전체 파이프라인 성공, 자막 실패 시 음성 인식 폴백, 각 단계 실패 시나리오 모킹)
- [x] 8 처리 파이프라인 구현

## 태스크 9: API 엔드포인트 구현

- [x] 9.1 `app/api/routes.py` 생성 - `POST /summarize` 엔드포인트 (URL 검증, 작업 생성, 백그라운드 태스크 시작, 202 응답)
- [x] 9.2 `app/api/routes.py`에 `GET /tasks/{task_id}` 엔드포인트 추가 (작업 상태 조회, 404 처리)
- [x] 9.3 `app/tests/test_api.py` 생성 - Property 4 (성공 응답 필수 필드 포함) 속성 테스트 구현
  - [x] 9.3.1 Property 4: 임의의 SummaryResult 직렬화 시 필수 필드 포함 검증 `[PBT: Property 4]`
- [x] 9.4 `app/tests/test_api.py`에 Property 5 (오류 응답 필수 필드 포함) 속성 테스트 추가
  - [x] 9.4.1 Property 5: 임의의 ErrorDetail 직렬화 시 필수 필드 포함 검증 `[PBT: Property 5]`
- [x] 9.5 `app/tests/test_api.py`에 단위 테스트 추가 (유효/무효 URL 요청, 작업 조회 성공/404, 오류 응답 형식)
- [x] 9 API 엔드포인트 구현

## 태스크 10: 오류 처리 및 로깅

- [x] 10.1 `app/main.py`에 글로벌 예외 핸들러 구현 (500 내부 오류, 504 타임아웃, 스택 트레이스 로깅)
- [x] 10.2 요청/응답 로깅 미들웨어 구현 (구조화된 JSON 로깅)
- [x] 10.3 `app/tests/test_error_handling.py` 생성 - 오류 처리 단위 테스트 (500, 502, 504 상태 코드 검증, 로깅 검증)
- [x] 10 오류 처리 및 로깅
