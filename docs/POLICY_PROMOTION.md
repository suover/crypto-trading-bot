# Ranking Policy Promotion

이 문서는 immutable Research Policy Candidate가 Shadow evidence와 사람의 승인을 거쳐 Full LIVE ranking policy로 활성화되는 lifecycle의 source of truth입니다. 연구 방법은 [STRATEGY_RESEARCH.md](STRATEGY_RESEARCH.md)를 참고하세요.

Python CLI는 repository root에서 `cd trading-engine`한 뒤 실행합니다. 권장 형식은 `uv run python -m scripts.<module> ...`이며, 아래의 `python -m` 예시는 engine 가상환경이 활성화된 경우의 축약형입니다.

## 핵심 경계

```text
Evidence != Decision
Gate eligibility != Enrollment
Shadow review eligibility != Human approval
Human approval != Full LIVE activation
Code deployment != Policy activation
Policy activation != Order
```

어떤 연구 command도 Upbit 주문을 만들지 않습니다. Full LIVE activation도 주문을 만들지 않고 다음 Market Universe 분석에서 사용할 ranking policy만 선택합니다. OpenAI recommendation, Telegram approval, order limits, Order Chance, idempotency와 reconciliation은 그대로 유지됩니다.

## Lifecycle

```mermaid
flowchart TD
    C[Immutable Research Policy Candidate]
    H[Registration-bounded Historical Evidence]
    F[Forward Gross / Turnover / Cost Evidence]
    G{Policy Promotion Gate}
    E[Shadow Enrollment]
    S[Shadow Selection Evaluations]
    P[Shadow Performance Evidence]
    R{Shadow Review Gate}
    A[Human-approved Promotion]
    L[Explicit Full LIVE Activation]
    N[Next Market Universe run]
    T[Append-only Termination]
    B[BASELINE policy]

    C --> H
    C --> F
    H --> G
    F --> G
    G -->|eligible| E --> S --> P --> R
    R -->|eligible for review| A --> L --> N
    L --> T --> B
```

## Candidate와 registration boundary

`ResearchPolicyCandidate`는 scenario definition, component weights, baseline policy signature, effective Top N, reference snapshot lineage와 registration watermarks를 immutable하게 저장합니다. 동일 context/definition 재실행은 기존 row를 검증해 반환하고 alias/conflict는 거부합니다.

Historical evidence는 registration 당시 알 수 있었던 source만 사용하고 genuine Forward evidence는 strict triple-cutoff 이후 snapshot만 사용합니다. Candidate row와 canonical signature가 source of truth이며 scenario file을 나중에 다시 해석하지 않습니다.

## Policy Promotion Gate

`PolicyPromotionGateService`는 하나의 candidate에 대해 bounded Historical evidence와 Forward Gross/Turnover/Cost-adjusted evidence를 함께 검증합니다. Result는 다음을 구분합니다.

- `INVALID_PROMOTION_DATA`: lineage, signature, chronology 또는 identity 위반
- `INSUFFICIENT_DATA`: 관찰 기간이나 표본 부족
- `NOT_ELIGIBLE`: 충분한 표본에서 고정 engineering threshold 미충족
- `ELIGIBLE_FOR_REVIEW`: Shadow 검토 시작 자격 충족

Gate는 deterministic engineering guardrail이며 통계적 유의성 검정이 아닙니다. 실행 시작 시 broad Forward context ceiling을 한 번 고정해 component가 같은 표본 시점을 사용합니다. CLI는 policy threshold/as-of override를 받지 않습니다.

```bash
cd trading-engine
python -m scripts.evaluate_policy_promotion_gate --candidate-id <ID>
```

Gate는 DB write, external API, enrollment, runtime policy 변경이나 주문을 수행하지 않습니다.

## Shadow Enrollment

신규 enrollment의 preview/apply는 current Policy Promotion Gate를 fresh evaluation하고 정확히 eligible인 candidate만 immutable Shadow 시작 anchor로 등록합니다. Candidate마다 enrollment는 하나입니다. 이미 정상 enrollment가 존재하면 기존 row와 provenance를 검증하고 Gate를 다시 평가하지 않은 채 `ALREADY_ENROLLED`를 반환합니다. 신규 row에는 Gate policy/check/evidence provenance와 decision signature를 저장하며, Gate와 enrollment 사이에 생긴 snapshot도 새 watermark에 포함해 Shadow evidence에서 제외합니다.

```bash
python -m scripts.enroll_shadow_policy --candidate-id <ID>
python -m scripts.enroll_shadow_policy --candidate-id <ID> --apply
```

Enrollment는 runtime activation이 아니며 Market Universe, AI, Telegram, 주문 또는 LIVE policy를 바꾸지 않습니다.

## Shadow Review Monitoring Phase 3

Phase 2가 등록하고 Phase 1이 Shadow에 등록한 후보는 기존 Shadow Selection 및 Performance 흐름에서 평가됩니다. Phase 3 Worker는 Enrollment ID를 제한된 배치로 순회하고 기존 Shadow Review Gate를 실행합니다. 정책과 결정 서명, 저장된 Enrollment 및 성과 근거, 검증 항목 전체를 확인합니다. 현재 설정과 기준 정책·Top N이 일치하며 활성 Full LIVE 정책과 충돌하지 않는 미승인 후보만 알림 대상으로 삼습니다.

Outbox의 중복 키는 Enrollment ID, Review Policy Signature, 정규화된 숫자 수신 대상입니다. Decision Signature가 새 증거로 바뀌어도 동일 키의 이력은 유지합니다. 전송 직전에는 현재 Gate 및 Human Approval을 다시 확인합니다. 현재 자격이 사라진 항목은 `SUPPRESSED`로 종료합니다.

`PENDING` 또는 재시도 시각에 도달한 `FAILED`만 `SENDING`으로 claim합니다. Claim을 커밋한 다음 Telegram을 호출하고, 검증된 영수증이 있으면 `SENT`에 Message ID와 시각을 기록합니다. 전송 여부가 불확실하거나 오래된 claim은 `UNCERTAIN`으로 보류합니다. Telegram 성공 후 DB 저장이 실패할 수 있어 외부 API와 DB 사이의 exactly-once는 보장되지 않습니다.

알림은 예를 들어 “Shadow Review Gate를 통과한 정책을 검토해 주세요. Candidate ID: 12, Shadow Enrollment ID: 5, UPBIT / KRW, Top N: 7, 상태: ELIGIBLE_FOR_PROMOTION_REVIEW”처럼 표시합니다. 실제 메시지에는 검증된 전략명, PASS 수, 근거 기준 시각 및 미승인 안내를 포함합니다. Telegram 매매 승인 Listener와는 별도이며 정책 승인과 LIVE 활성화는 기존 수동 절차를 따릅니다. Phase 4의 정책 검토 버튼·콜백은 별도 개발 범위입니다.

## Research Lifecycle 자동 검증과 조건부 등록

`research-lifecycle-worker`는 등록된 `ResearchPolicyCandidate`를 ID 순서로 배치 조회하고 기존 Promotion Gate와 Shadow Enrollment 서비스를 실행합니다. [Phase 2 Worker](OPERATIONS.md#research-candidate-automation-phase-2)가 등록한 후보도 이 흐름에 들어가며, Forward 표본은 등록 watermark 이후로 제한됩니다. Phase 1은 PostgreSQL만 사용합니다.

기본값은 `RESEARCH_LIFECYCLE_ENABLED=false`, `RESEARCH_AUTO_SHADOW_ENROLLMENT_ENABLED=false`입니다. 자동 검증과 자동 등록은 별도 opt-in이며 Apply에는 두 flag가 모두 필요합니다. 기존 유효한 enrollment는 provenance를 검증해 `ALREADY_ENROLLED`로 처리하고 Gate를 재평가하지 않습니다. 손상된 enrollment는 `INVALID_SHADOW_ENROLLMENT`입니다.

- `INSUFFICIENT_DATA`: 증거가 쌓일 때까지 대기
- `NOT_ELIGIBLE`: 현재 기준 미충족, 등록 안 함
- `INVALID_PROMOTION_DATA`: 무결성 오류 보고, 등록 안 함
- `ELIGIBLE_FOR_REVIEW`: 기존 Enrollment 서비스로 재검증; Preview는 `DRY_RUN`, Apply는 실제 `CREATED`인 경우에만 commit

등록 직전 Gate 상태가 바뀌면 최신 상태를 보고하고 등록하지 않습니다. 후보마다 별도 session/transaction을 사용해 실패를 격리하며, 오류 원문 대신 예외 종류와 구조화된 후보별 상태를 출력합니다. 새 평가 이력 테이블은 만들지 않습니다.

`trading-engine/`에서 실행합니다.

```bash
# 항상 읽기 전용. Lifecycle 비활성 상태에서도 진단 가능
uv run python -m scripts.run_research_lifecycle_worker --once

# 두 flag를 모두 활성화한 환경에서만 조건부 등록
uv run python -m scripts.run_research_lifecycle_worker --once --apply
```

`--once`는 자동 등록 flag가 켜져 있어도 Preview입니다. 상시 worker는 Lifecycle flag가 꺼져 있으면 DB 조회 없이 idle합니다. Lifecycle만 켜면 주기적 읽기 전용 검증, 두 flag를 모두 켜면 조건부 Shadow 등록까지 수행합니다. `RESEARCH_LIFECYCLE_INTERVAL_SECONDS=21600`(60–604800초), `RESEARCH_LIFECYCLE_BATCH_SIZE=10`(1–500개)이며 범위 밖 설정은 거부합니다.

한 순회의 최대 candidate ID를 고정하고 메모리 cursor로 배치를 순회합니다. 뒤쪽 후보와 오류 후보도 다음 순회에서 다시 검사하며 새 후보는 이후 순회에서 발견합니다. 재시작하면 cursor는 처음으로 돌아가지만 기존 enrollment는 중복 생성하지 않습니다. 실패한 cycle도 전체 interval을 기다리고 재시도합니다. 전용 PostgreSQL advisory lock은 중복 worker 실행을 막습니다.

등록 이후 selection 평가는 기존 `recommendation-outcome-worker`의 `SHADOW_SELECTION_EVALUATION_ENABLED`로 별도 제어합니다. 자동화의 끝은 Shadow Enrollment입니다. Shadow Review 통과, 사람의 Promotion Approval 또는 Full LIVE Activation을 수행하거나 암시하지 않습니다. 현재 Ranking, AI 추천과 Telegram 매매 승인에는 영향이 없습니다. Telegram 전략 승인 및 자동화 확장은 후속 단계이며, 현재 explicit Full LIVE activation 절차는 그대로 유지합니다.

## Shadow Selection

`ShadowPolicyEvaluationService`는 enrollment의 ID/captured/time watermarks를 모두 strict `>`로 통과한 snapshot만 candidate policy로 offline replay합니다. 같은 dataset의 broad timeline을 먼저 고정하고 baseline/Top N mismatch와 replay failure도 immutable marker로 저장해 앞뒤 chronology를 인위적으로 연결하지 않습니다.

```bash
python -m scripts.evaluate_shadow_policy_selection --candidate-id <ID>
python -m scripts.evaluate_shadow_policy_selection --candidate-id <ID> --apply
```

Preview는 write가 없고 apply는 selection evidence만 저장합니다. `recommendation-outcome-worker`는 `SHADOW_SELECTION_EVALUATION_ENABLED=true`일 때 기존 enrollment를 자동 평가할 수 있지만 enrollment나 promotion은 자동 생성하지 않습니다.

## Shadow Performance

저장된 baseline/shadow Top N과 existing candidate outcome을 결합해 Gross, Turnover, Cost-adjusted evidence를 조회 시점에 계산합니다. Selection을 다시 계산하지 않고 performance row를 저장하지 않습니다. 한 invocation의 as-of와 evaluation ceiling을 component 전체가 공유합니다.

```bash
python -m scripts.evaluate_shadow_policy_performance \
  --candidate-id <ID> \
  --horizon 60 --horizon 240 --horizon 1440 \
  --fee-rate 0.0005 --spread-cost-rate 0.0005 --slippage-rate 0.001
```

Context mismatch나 replay failure는 continuity break이며 첫 valid selection에는 이전 turnover/cost를 붙이지 않습니다.

## Shadow Review Gate

`ShadowReviewGateService`는 enrollment에 동결된 pre-Shadow provenance를 검증하고 immutable Shadow Performance Evidence에 고정된 policy를 적용합니다. Historical/Forward evidence나 Offline Replay를 다시 실행하지 않습니다.

Result는 invalid, insufficient, fail, eligible-for-promotion-review를 구분합니다. Eligible은 사람이 검토할 수 있다는 의미일 뿐 자동 promotion이 아닙니다.

```bash
python -m scripts.evaluate_shadow_review_gate --candidate-id <ID>
```

Gate는 DB write, external call, runtime 또는 LIVE 변경을 하지 않습니다.

## Human-approved Promotion

사람이 preview의 exact review decision signature와 evidence를 확인한 뒤에만 immutable approval row를 기록합니다. Apply는 Review Gate를 다시 평가하며 signature가 달라지면 `REVIEW_DECISION_CHANGED`로 fail-closed합니다. `--force` 우회는 없습니다.

```bash
# Preview
python -m scripts.approve_shadow_policy_promotion --candidate-id <ID>

# Apply
python -m scripts.approve_shadow_policy_promotion \
  --candidate-id <ID> \
  --expected-review-decision-signature "<EXACT_SIGNATURE_FROM_PREVIEW>" \
  --apply
```

Approval은 최종 사람 감사 기록이지만 activation은 아닙니다. Ranking runtime, scheduler, order path를 변경하지 않습니다.

## Full LIVE Activation

Full LIVE activation은 현재 구현된 explicit operator action입니다. 승인된 `ShadowPolicyPromotionApproval`을 source로 user/exchange/quote scope에 하나의 활성 policy를 기록합니다.

```bash
# Preview
python -m scripts.activate_full_live_policy \
  --promotion-approval-id <APPROVAL_ID> \
  --user-id <USER_ID> --exchange UPBIT --quote-asset KRW

# Apply
python -m scripts.activate_full_live_policy \
  --promotion-approval-id <APPROVAL_ID> \
  --user-id <USER_ID> --exchange UPBIT --quote-asset KRW \
  --expected-approval-signature "<SIGNATURE_FROM_PREVIEW>" \
  --apply
```

Apply 전에 Production DB를 백업하고 preview signature, scope, active policy 부재와 approval provenance를 사람이 확인해야 합니다. Activation은 order를 만들거나 기존 recommendation을 재실행하지 않습니다. 다음 Market Universe build에서 resolver가 해당 policy를 검증한 뒤 사용합니다.

현재 effective policy는 read-only로 확인합니다.

```bash
python -m scripts.check_live_ranking_policy \
  --user-id <USER_ID> --exchange UPBIT --quote-asset KRW
```

Active activation이 없거나 종료됐다면 BASELINE이 정상 결과입니다.

## Full LIVE Termination

종료는 activation row를 수정하거나 삭제하지 않습니다. Exact activation signature를 preview/apply 사이에 확인하고 immutable termination event를 append합니다. 다음 resolution은 BASELINE으로 돌아갑니다.

```bash
# Preview
python -m scripts.stop_full_live_policy \
  --activation-id <ACTIVATION_ID> \
  --user-id <USER_ID> --exchange UPBIT --quote-asset KRW

# Apply
python -m scripts.stop_full_live_policy \
  --activation-id <ACTIVATION_ID> \
  --user-id <USER_ID> --exchange UPBIT --quote-asset KRW \
  --expected-activation-signature "<SIGNATURE_FROM_PREVIEW>" \
  --reason "operator decision" --apply
```

Termination도 order 취소나 기존 LIVE order mutation을 수행하지 않습니다. 기존 order는 reconciliation과 runbook 절차로 별도 관리합니다.

## 단계별 side effect

| 단계 | DB write | External API | Runtime ranking 변경 | Order 생성 |
| --- | --- | --- | --- | --- |
| Historical/Forward evidence | 없음 | 없음 | 없음 | 없음 |
| Promotion Gate | 없음 | 없음 | 없음 | 없음 |
| Shadow Enrollment preview | 없음 | 없음 | 없음 | 없음 |
| Shadow Enrollment apply | enrollment INSERT | 없음 | 없음 | 없음 |
| Shadow Selection apply | evaluation INSERT | 없음 | 없음 | 없음 |
| Shadow Performance/Review | 없음 | 없음 | 없음 | 없음 |
| Human Approval apply | approval INSERT | 없음 | 없음 | 없음 |
| Full LIVE Activation apply | activation INSERT | 없음 | 다음 분석부터 | 없음 |
| Full LIVE Termination apply | termination INSERT | 없음 | BASELINE 복귀 | 없음 |

## Integrity와 concurrency

- Candidate, enrollment, evaluation, approval와 activation provenance는 canonical signature로 검증합니다.
- Preview/apply는 exact expected signature로 source 변경을 탐지합니다.
- Unique constraint와 conflict handling이 동일 command의 중복 row를 막습니다.
- Watermark, timezone-aware as-of와 maximum ID ceiling이 실행 중 추가된 snapshot 혼입을 막습니다.
- Context mismatch와 invalid integrity는 누락시키지 않고 fail-closed status로 남깁니다.

## 현재 promotion model

현재 lifecycle에는 Canary runtime이 없습니다. Migration history의 Canary table은 과거 schema이고 cleanup migration이 promotion flow를 제거했습니다. 현재 경로는 Shadow human approval 뒤 운영자의 explicit Full LIVE activation입니다.

환경별 candidate ID나 현재 Production row 상태는 이 문서의 계약이 아닙니다. 현재 상태는 diagnostic CLI와 해당 DB에서 확인합니다.
