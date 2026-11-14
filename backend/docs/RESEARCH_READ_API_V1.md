# Research Read API v1

## 공통 계약

- Base URL: 로컬 기본 `http://127.0.0.1:8080/api/v1/research`.
- 모든 endpoint는 세션 인증된 ADMIN에게만 허용합니다. 공개 HTTP 사용은 금지하고 외부 서비스 전환 전 TLS를 구성합니다.
- 서버의 `BACKEND_TRADING_USER_ID`, `BACKEND_EXCHANGE`(UPBIT), `BACKEND_QUOTE_ASSET`(KRW)가 범위를 결정합니다. 요청으로 사용자나 범위를 바꿀 수 없으며 알 수 없는 query parameter는 400입니다.
- user가 없거나 inactive이면 403입니다. 범위 내 다른 schema/policy/Top N의 기록을 임의로 하나로 합쳐 자동화 상태를 판단하지 않습니다.
- UTC ISO-8601 시각과 JSON number를 사용합니다. ID는 Java Long, 가중치는 BigDecimal입니다. 클라이언트는 JS number 변환의 정밀도 제한을 고려해야 합니다.
- `generatedAt`은 응답 생성 시각이며 데이터의 관측 시각이 아닙니다. `capturedAt`, `registeredAt`, `storedGateEvaluatedAt`, 체크포인트 시각을 함께 해석하세요.
- 한 service 호출은 REPEATABLE_READ/readOnly 트랜잭션으로 count·페이지·상세 사이의 DB 조회 시점을 일치시킵니다. 별도 HTTP 요청 사이에는 새 데이터가 추가될 수 있습니다.
- raw JSON, secret, 감사 context, decision signature, ORM Entity를 반환하지 않습니다. Baseline/policy signature는 인증된 관리자가 연구 cohort를 구분하는 식별 정보로만 노출합니다.
- 이 API는 등록된 데이터의 조회이며 Python의 provenance signature 검증이나 현재 Gate 재평가를 대신하지 않습니다.
- 업무 쓰기 endpoint, 회원가입/JWT, policy approve/activate, 거래·포트폴리오 API는 없습니다.

## 오류

```json
{
  "code": "INVALID_REQUEST",
  "message": "Invalid request parameters",
  "generatedAt": "2026-01-01T00:00:00Z"
}
```

| HTTP | code | 의미 |
| --- | --- | --- |
| 400 | INVALID_REQUEST | 파라미터 형식/범위, 날짜 순서, sort 또는 알 수 없는 query |
| 401 | UNAUTHENTICATED | 인증 세션 없음/만료, JSON 오류 (Basic challenge 없음) |
| 403 | FORBIDDEN | ADMIN 아님, 비활성/없는 configured user, 미정의 경로/쓰기 요청 |
| 404 | NOT_FOUND | 후보 없음 또는 다른 사용자/거래소/quote 범위 |
| 500 | INTERNAL_ERROR | DB 장애/매핑 오류 등, 정상 0건이나 빈 목록으로 위장하지 않음 |

미인증 요청은 쓰기/미정의 경로에서도 401이 우선될 수 있습니다. 서버 예외 원문과 Stack Trace는 응답에 포함하지 않습니다.

## 1. GET /summary

파라미터 없음. `Summary`:

```json
{
  "scope": {"userId": 1, "exchange": "UPBIT", "quoteAsset": "KRW"},
  "candidateCount": 3,
  "shadowEnrollmentCount": 1,
  "snapshotCount": 10,
  "latestSnapshotId": 123,
  "latestSnapshotCapturedAt": "2026-01-01T01:00:00Z",
  "generatedAt": "2026-01-01T01:05:00Z"
}
```

candidate와 snapshot은 해당 범위의 전체 저장 수입니다. Enrollment는 해당 범위 후보와 user/exchange/quote/dataset/baseline/Top N이 일치하는 저장 enrollment의 존재로 셉니다. 최신 snapshot은 `capturedAt DESC, id DESC`입니다. 데이터가 없으면 count는 0이고 latest ID/time은 null입니다.

## 2. GET /candidates

| 파라미터 | 기본 | 검증과 의미 |
| --- | --- | --- |
| keyword | 없음 | scenario_name 포함 검색, 대소문자 무시, 최대 80자, trim 후 빈 값은 필터 없음 |
| registeredFrom | 없음 | ISO-8601 offset timestamp, 포함 하한 |
| registeredTo | 없음 | ISO-8601 offset timestamp, 포함 상한, from보다 이르면 400 |
| shadowEnrolled | 없음 | true/false, 현재 저장 enrollment 존재 여부 |
| page | 0 | 0 이상 정수 |
| size | 20 | 1–100 정수 |
| sort | registeredAt,desc | registeredAt/id/policyName + asc/desc만 허용 |

QueryDSL 파라미터 바인딩으로 조회하며 keyword의 `%`, `_`도 literal 문자로 검색합니다. Shadow 여부는 correlated EXISTS이고 JOIN으로 후보를 중복시키지 않습니다. 모든 정렬에는 ID DESC tie-breaker가 적용됩니다(id 자체로 정렬하면 해당 방향으로 고유 정렬). 날짜 비교는 두 경계 모두 포함합니다.

```http
GET /api/v1/research/candidates?keyword=momentum&shadowEnrolled=true&page=0&size=20&sort=registeredAt,desc
```

`Page<CandidateItem>`:

```json
{
  "items": [
    {
      "id": 201,
      "policyName": "Momentum A",
      "registeredAt": "2026-01-01T00:00:00Z",
      "exchange": "UPBIT",
      "quoteAsset": "KRW",
      "effectiveTopN": 7,
      "shadowEnrolled": true
    }
  ],
  "page": 0,
  "size": 20,
  "totalElements": 1,
  "totalPages": 1,
  "generatedAt": "2026-01-01T01:05:00Z"
}
```

검색 결과 없음은 200, `items=[]`, `totalElements=0`, `totalPages=0`입니다. 마지막 페이지 밖이면 items만 비고 total은 실제 전체 검색 건수로 유지됩니다.

## 3. GET /candidates/{id}

id는 양의 Long. Query parameter 없음. 다른 scope의 후보도 존재 여부를 구분하지 않고 404입니다.

`CandidateDetail` 필드:

| 필드 | 의미 |
| --- | --- |
| id, policyName, registeredAt | registry 후보 ID, scenario_name, 등록 시각 |
| exchange, quoteAsset, effectiveTopN | 저장된 연구 범위 |
| referenceSnapshotId | 등록 시 사용한 기준 snapshot |
| componentWeights | JSONB 가중치, BigDecimal 값의 key/value 객체 |
| registrationContext.datasetSchemaVersion | 등록 기준 dataset version |
| registrationContext.baselinePolicySignature | 기준 policy의 식별 signature |
| registrationContext.snapshotIdWatermark | 등록 당시 snapshot ID watermark |
| registrationContext.capturedAtWatermark | 등록 당시 captured-at watermark |
| registrationContext.referenceSnapshotCapturedAt | reference snapshot 캡처 시각 |
| shadowEnrolled | scope/context가 일치하는 enrollment 존재 |
| enrollment | 없으면 null, 있으면 아래 과거 기록 |
| generatedAt | 응답 생성 시각 |

`enrollment`는 `{id, shadowEnrolledAt, storedGateStatus, storedGateEvaluatedAt}`입니다. **storedGateStatus는 등록 당시 Gate 상태이며 현재 재평가 결과가 아닙니다.** Approval/Full LIVE 여부도 의미하지 않습니다.

componentWeights의 키는 Python 원본 이름(예: `trend_alignment`, `volume_confirmation`)을 유지합니다. 원본 JSONB가 numeric 또는 Decimal string이어도 BigDecimal로 읽어 precision을 유지합니다. 손상된 JSON은 500으로 실패하며 임의의 0을 반환하지 않습니다.

## 4. GET /snapshots

`page=0`, `size=20`만 지원하며 후보 목록과 같은 범위 검증을 적용합니다. 정렬은 고정 `capturedAt DESC, id DESC`입니다.

`Page<SnapshotItem>`의 items:

| 필드 | 의미 |
| --- | --- |
| id | replay snapshot ID |
| capturedAt | dataset 관측 시각 |
| createdAt | 저장 시각 |
| policySignature | 저장 policy 식별 정보 |
| researchCandidateCount | 저장된 research 후보 수 |
| prefilterCandidateCount | 저장된 prefilter 후보 수 |
| rankedCandidateCount | 저장된 RANKED 후보 수 |
| finalCandidateCount | 저장된 최종 후보 수 |

후보 수는 저장 컬럼을 그대로 읽습니다. 현재 시장 데이터를 다시 수집하거나 ranking하지 않습니다.

## 5. GET /automation-status

파라미터 없음. `AutomationStatus`:

```json
{
  "scope": {"userId": 1, "exchange": "UPBIT", "quoteAsset": "KRW"},
  "observed": false,
  "states": [],
  "generatedAt": "2026-01-01T01:05:00Z"
}
```

관측된 상태가 있으면 observed=true이며 모든 해당 복합키 row를 반환합니다. observed는 **row 존재 여부**로서 worker 가동/정상 여부나 다음 등록 자격을 나타내지 않습니다.

각 `AutomationItem`:

- scope: userId/exchange/quoteAsset.
- datasetSchemaVersion, baselinePolicySignature, effectiveTopN: 위 scope와 함께 복합 PK.
- lastObservedSnapshotId, reviewedSnapshotId, reviewStatus: 필수 저장 체크포인트.
- lastStatus, lastAttemptAt, lastReferenceSnapshotId: nullable 최근 시도 정보.
- lastRegistrationStatus, lastRegisteredSnapshotId, lastRegisteredAt: nullable 최근 등록 정보.

정렬은 datasetSchemaVersion → baselinePolicySignature → effectiveTopN 오름차순입니다. nullable 컬럼은 null로 유지하며 임의 status나 0으로 대체하지 않습니다. 서로 다른 policy/version/Top N 기록을 최신 한 건으로 축소하지 않습니다. Python의 21-snapshot 조건, watermark, 후보 적격성 판단을 재구현하지 않습니다.

## 프론트엔드와 운영 경계

현재 frontend는 Mock DashboardRepository를 사용하며 이번 API에 아직 연결되지 않았습니다. historical/forward/Shadow performance, approval, activation 등의 전체 화면 데이터를 이 5개 API가 제공한다고 가정하지 마세요. 서버 설정 scope, BigDecimal/Long 정밀도, HTTP 오류, empty/null, 과거 Gate 표시를 유지하는 adapter가 다음 작업입니다.

설정, 로컬 실행, 최소 권한과 Testcontainers 검증은 [Backend README](../README.md)를 참고하세요. Production Compose, 스키마, 매매 엔진은 변경하지 않습니다.
