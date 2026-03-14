# 구현 계획: API Key 인증

## 개요

YouTube Summary API에 API 키 기반 인증 시스템을 구현한다. API 키 저장소, 인증 의존성, 관리 엔드포인트를 순차적으로 구현하고, 기존 엔드포인트에 인증을 적용한 뒤 통합 테스트로 마무리한다.

## 태스크

- [ ] 1. API 키 저장소 구현
  - [ ] 1.1 `app/services/api_key_store.py` 파일 생성: `ApiKeyStore` 클래스 구현
    - `hash_key` 정적 메서드: SHA-256 해시 계산
    - `mask_key` 정적 메서드: 마지막 4자리만 노출, 나머지 `*` 마스킹
    - `create_key(client_name)`: UUID v4 키 생성, 해시 저장, `_hash_index` 매핑 추가
    - `validate_key(raw_key)`: 해시로 키 조회, 메타데이터 또는 None 반환
    - `list_keys()`: 모든 키 메타데이터 반환 (마스킹된 키 포함)
    - `revoke_key(key_id)`: 키 비활성화, 성공 시 True / 미존재 시 False
    - _Requirements: 1.1, 1.2, 1.3, 2.2, 2.4, 2.5, 3.1, 3.2, 3.3, 3.4, 3.5, 7.3, 7.4_

  - [ ]* 1.2 Property 1 테스트: API 키 생성 라운드트립
    - **Property 1: API 키 생성 라운드트립**
    - 임의의 비어있지 않은 클라이언트 이름으로 `create_key` 호출 시 UUID v4 형식 키 반환, 메타데이터에 올바른 client_name, created_at, is_active=True 포함 검증
    - **Validates: Requirements 1.1, 1.2, 1.3**

  - [ ]* 1.3 Property 2 테스트: API 키 해시 라운드트립
    - **Property 2: API 키 해시 라운드트립**
    - 생성된 원본 키로 `validate_key` 호출 시 메타데이터 반환, 저장소 내부에 원본 키 대신 SHA-256 해시만 저장 검증
    - **Validates: Requirements 2.2, 7.3, 7.4**

  - [ ]* 1.4 Property 3 테스트: 유효하지 않은 키 거부
    - **Property 3: 유효하지 않은 키 거부**
    - 저장소에 등록되지 않은 임의의 문자열로 `validate_key` 호출 시 None 반환 검증
    - **Validates: Requirements 2.4**

  - [ ]* 1.5 Property 4 테스트: 키 비활성화 후 인증 거부
    - **Property 4: 키 비활성화 후 인증 거부**
    - 키 생성 후 `revoke_key`로 비활성화, `validate_key` 호출 시 is_active=False 검증
    - **Validates: Requirements 2.5, 3.4**

  - [ ]* 1.6 Property 5 테스트: 키 목록 조회 정확성
    - **Property 5: 키 목록 조회 정확성**
    - N개(1~10) 키 생성 후 `list_keys` 호출 시 길이 N, 각 항목에 key_id, masked_key, client_name, created_at, is_active 필드 포함 검증
    - **Validates: Requirements 3.1, 3.2**

  - [ ]* 1.7 Property 6 테스트: 키 마스킹 정확성
    - **Property 6: 키 마스킹 정확성**
    - 임의의 UUID v4 문자열에 `mask_key` 적용 시 마지막 4자리 일치, 나머지 `*` 마스킹 검증
    - **Validates: Requirements 3.3**

  - [ ]* 1.8 Property 7 테스트: 존재하지 않는 키 삭제 시 실패
    - **Property 7: 존재하지 않는 키 삭제 시 실패**
    - 저장소에 등록되지 않은 임의의 UUID로 `revoke_key` 호출 시 False 반환 검증
    - **Validates: Requirements 3.5**

- [ ] 2. 데이터 모델 추가
  - [ ] 2.1 `app/models/requests.py`에 `CreateApiKeyRequest` 모델 추가
    - `client_name: str` 필드 (필수, 빈 문자열/공백만 있는 문자열 거부를 위한 validator 포함)
    - _Requirements: 1.5_

  - [ ] 2.2 `app/models/responses.py`에 인증 관련 응답 모델 추가
    - `ApiKeyResponse`: key_id, api_key, client_name, created_at, is_active
    - `ApiKeyInfo`: key_id, masked_key, client_name, created_at, is_active
    - `ApiKeyListResponse`: keys (list[ApiKeyInfo])
    - _Requirements: 1.1, 3.1, 3.2, 3.3_

- [ ] 3. 체크포인트 - 저장소 및 모델 검증
  - 모든 테스트를 실행하여 통과 여부를 확인하고, 문제가 있으면 사용자에게 질문한다.

- [ ] 4. 인증 의존성 구현
  - [ ] 4.1 `app/api/dependencies.py` 파일 생성: `verify_api_key` 함수 구현
    - `X-API-Key` 헤더에서 키를 추출하여 `ApiKeyStore.validate_key`로 검증
    - 헤더 누락 시 401 (MISSING_API_KEY), 유효하지 않은 키 시 401 (INVALID_API_KEY), 비활성 키 시 403 (DISABLED_API_KEY) 반환
    - 인증 실패 시 WARNING 레벨 로그 기록 (마스킹된 키 포함)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 6.1, 6.2, 6.3, 6.4, 7.1, 7.2_

  - [ ] 4.2 `app/api/dependencies.py`에 `verify_admin_key` 함수 구현
    - `X-Admin-Key` 헤더 값을 `ADMIN_API_KEY` 환경변수와 `hmac.compare_digest`로 비교
    - 불일치 시 403 (FORBIDDEN) 반환
    - `ADMIN_API_KEY` 미설정 시 503 반환
    - _Requirements: 1.4, 4.1, 4.2, 4.4, 6.5_

- [ ] 5. 관리 라우터 구현
  - [ ] 5.1 `app/api/admin_routes.py` 파일 생성: 관리 엔드포인트 구현
    - `POST /admin/api-keys`: `verify_admin_key` 의존성, `CreateApiKeyRequest` 검증, 키 발급 후 `ApiKeyResponse` 반환
    - `GET /admin/api-keys`: `verify_admin_key` 의존성, 키 목록 조회 후 `ApiKeyListResponse` 반환
    - `DELETE /admin/api-keys/{key_id}`: `verify_admin_key` 의존성, 키 비활성화, 미존재 시 404 (KEY_NOT_FOUND)
    - _Requirements: 1.1, 3.1, 3.4, 3.5_

  - [ ] 5.2 `app/main.py` 수정: 관리 라우터 등록 및 기존 미들웨어 정리
    - 관리 라우터(`admin_routes.router`)를 앱에 등록
    - `ADMIN_API_KEY` 환경변수 미설정 시 경고 로그 기록
    - 기존 단일 API_KEY 미들웨어 인증 로직 제거 (Depends 기반으로 전환)
    - _Requirements: 4.1, 4.3_

  - [ ]* 5.3 Property 8 테스트: 빈 클라이언트 이름 거부
    - **Property 8: 빈 클라이언트 이름 거부**
    - 공백 문자로만 구성된 임의의 문자열(빈 문자열 포함)로 `POST /admin/api-keys` 요청 시 422 반환 검증
    - **Validates: Requirements 1.5**

  - [ ]* 5.4 Property 9 테스트: 잘못된 관리자 키 거부
    - **Property 9: 잘못된 관리자 키 거부**
    - 실제 관리자 키와 다른 임의의 문자열로 관리 엔드포인트 접근 시 403 FORBIDDEN 반환 검증
    - **Validates: Requirements 1.4, 4.2**

- [ ] 6. 기존 엔드포인트 인증 적용
  - [ ] 6.1 `app/api/routes.py` 수정: 보호된 엔드포인트에 인증 의존성 추가
    - `POST /summarize`에 `dependencies=[Depends(verify_api_key)]` 추가
    - `GET /tasks/{task_id}`에 `dependencies=[Depends(verify_api_key)]` 추가
    - _Requirements: 5.1, 5.2, 5.4_

  - [ ]* 6.2 Property 10 테스트: 인증 오류 응답 형식 일관성
    - **Property 10: 인증 오류 응답 형식 일관성**
    - 인증 실패 응답의 JSON 구조가 `ErrorResponse` 형식(error.code, error.message)인지 검증
    - **Validates: Requirements 6.1**

- [ ] 7. 체크포인트 - 인증 흐름 검증
  - 모든 테스트를 실행하여 통과 여부를 확인하고, 문제가 있으면 사용자에게 질문한다.

- [ ] 8. 인증 통합 테스트 및 마무리
  - [ ] 8.1 `app/tests/test_auth.py` 파일 생성: 단위 테스트 구현
    - 오류 코드 매핑 검증: MISSING_API_KEY, INVALID_API_KEY, DISABLED_API_KEY, FORBIDDEN 각 시나리오별 정확한 코드 반환
    - 보호된 엔드포인트 인증 검증: `POST /summarize`, `GET /tasks/{task_id}`에 API 키 없이 접근 시 401 반환
    - 키 발급 → 인증 → 비활성화 → 인증 거부 통합 시나리오
    - _Requirements: 2.3, 2.4, 2.5, 5.1, 5.2, 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ] 8.2 기존 테스트 수정: `app/tests/test_api.py`에 API 키 헤더 추가
    - 기존 테스트가 인증 적용 후에도 통과하도록 유효한 API 키 헤더를 테스트 요청에 추가
    - _Requirements: 5.1, 5.2_

  - [ ] 8.3 `.env.example`에 `ADMIN_API_KEY` 환경변수 추가
    - _Requirements: 4.1_

- [ ] 9. 최종 체크포인트 - 전체 테스트 통과 확인
  - 모든 테스트를 실행하여 통과 여부를 확인하고, 문제가 있으면 사용자에게 질문한다.

## 참고

- `*` 표시된 태스크는 선택 사항이며, 빠른 MVP를 위해 건너뛸 수 있습니다
- 각 태스크는 특정 요구사항을 참조하여 추적 가능합니다
- 체크포인트에서 점진적으로 검증합니다
- Property 테스트는 보편적 정확성 속성을 검증하고, 단위 테스트는 구체적 예시와 엣지 케이스를 검증합니다
