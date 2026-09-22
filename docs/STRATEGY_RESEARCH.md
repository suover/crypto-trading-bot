# Strategy Research와 Validation

이 문서는 ranking research subsystem의 방법론과 대표 CLI를 설명합니다. 정책을 Shadow와 Full LIVE로 승격하는 절차는 [POLICY_PROMOTION.md](POLICY_PROMOTION.md)를 참고하세요.

Python CLI는 repository root에서 `cd trading-engine`한 뒤 실행합니다. 권장 형식은 `uv run python -m scripts.<module> ...`이며, 아래의 `python -m` 예시는 engine 가상환경이 활성화된 경우의 축약형입니다.

## 해석 원칙

```text
ranking selection의 gross market movement != actual trading PnL
recommendation outcome != bot realized PnL
bot realized PnL != account-level portfolio performance
```

연구 도구는 수익성, 최적 전략, 통계적 유의성 또는 미래 성과를 보장하지 않습니다. 별도 명시가 없는 evaluator는 DB-only/read-only이며 Upbit, OpenAI, Telegram이나 주문 경로를 호출하지 않습니다. Scenario sweep의 상대 결과만으로 LIVE policy를 변경하지 않습니다.

## Data foundation

### Recommendation Outcome

최종 recommendation의 동일-pipeline 시작 가격과 target 시각까지 완전히 닫힌 1분봉 종가를 사용합니다. BUY/SELL은 action-aligned gross return, HOLD는 이후 시장 움직임을 저장합니다. HOLD를 WIN/LOSS 적중률로 해석하지 않으며 confidence는 모델 self-report입니다.

```bash
python -m scripts.rebuild_recommendation_outcomes --limit 10
python -m scripts.rebuild_recommendation_outcomes --limit 10 --apply
python -m scripts.report_recommendation_outcomes
```

Rebuild는 기본 dry-run이지만 public candle 조회를 수행할 수 있습니다. Report는 DB-only입니다.

### Strategy Replay Dataset

`STRATEGY_REPLAY_DATASET_ENABLED=false`가 기본입니다. 활성화한 DYNAMIC universe run은 당시 liquidity prefilter, HELD data-collection 후보, feature, ranking policy와 final selection provenance를 research snapshot/candidate로 저장합니다. 추가 external API나 GPT 호출은 하지 않습니다. 활성화 이전 데이터를 추측해서 backfill하지 않습니다.

```bash
python -m scripts.report_strategy_replay_dataset --limit 50
```

### Research Candidate Outcome

Rankable replay candidate의 60/240/1440분 future gross movement를 저장합니다. Frozen `latest_price`를 시작가로, 완전히 닫힌 Upbit 1분봉을 종료가로 사용합니다. HELD-only/non-rankable 후보는 public API 대상이 아닙니다. COMPLETE는 immutable하게 재조회하지 않고 retryable unavailable만 재시도합니다.

```bash
python -m scripts.rebuild_research_candidate_outcomes --limit 20
python -m scripts.rebuild_research_candidate_outcomes --snapshot-id 123 --apply
python -m scripts.report_research_candidate_outcomes --limit 50
```

이 기능은 public market-data 조회를 할 수 있지만 private API, 주문, OpenAI, Telegram은 사용하지 않습니다.

## Offline replay와 비교

### Offline Strategy Replay

Snapshot에 저장된 policy를 복원해 당시 persisted prefilter pool 안에서만 ranking을 다시 실행합니다. Rankable 조건은 `in_prefilter`, `buy_eligible`, `enough_candles`입니다. HELD augmentation은 replay Top N에 포함하지 않습니다. Baseline을 재현하지 못하거나 schema/policy/input이 손상되면 fail-closed합니다.

```bash
python -m scripts.replay_strategy_rankings --snapshot-id 123
python -m scripts.replay_strategy_rankings --latest 50 --top-n 5
```

Override는 component weight만 허용하고 finite/nonnegative이며 합이 정확히 1이어야 합니다. 지정하지 않은 field는 현재 default가 아니라 snapshot의 historical value를 유지합니다.

### Strategy A/B Performance

동일 snapshot에서 baseline과 explicit weight scenario가 선택한 동일 Top N을 COMPLETE candidate outcome으로 비교합니다. Top N 중 하나라도 outcome이 빠지면 일부 후보만 평균내지 않고 해당 snapshot을 실패 처리합니다. Equal-weight arithmetic mean, median, positive rate와 scenario-baseline delta는 gross 기술 통계입니다.

```bash
python -m scripts.evaluate_strategy_ab_performance --snapshot-id 123 --horizon 240
```

### Ranking Scenario Sweep

`ranking-scenario-sweep-v1` JSON의 명시적 scenario들을 동일 common comparable snapshot set에서 평가합니다. Cohort는 horizon, baseline policy signature와 effective Top N을 유지합니다. Composite score, winner, recommended policy를 만들지 않습니다.

```bash
python -m scripts.evaluate_ranking_scenario_sweep \
  --latest 100 \
  --horizon 60 --horizon 240 --horizon 1440 \
  --scenario-file examples/ranking_scenarios.example.json
```

예제 weight는 입력 형식 예시이며 추천 정책이 아닙니다. 반복 탐색은 같은 historical data에서 우연히 좋아 보이는 scenario를 찾을 위험을 높입니다.

## Temporal validation

### Holdout

모든 scenario가 동시에 SUCCESS인 common set을 시간순으로 Research/Holdout 구간에 나눕니다. Shuffle하지 않습니다. Ratio mode는 최신 일부를 holdout으로 두고, fixed cutoff mode는 `captured_at <= cutoff`와 `> cutoff`를 분리합니다.

```bash
python -m scripts.evaluate_ranking_holdout_validation \
  --latest 100 --horizon 240 \
  --scenario-file examples/ranking_scenarios.example.json \
  --holdout-ratio 0.30
```

Retrospective split은 scenario 설계 때 future data가 노출되지 않았음을 증명하지 않으므로 strict unseen으로 간주하지 않습니다.

### Walk-Forward

고정 scenario를 expanding Research window와 겹치지 않는 full Validation fold에서 반복 평가합니다. Partial tail은 fold로 만들지 않고 별도 count로 보고합니다. 이전 Validation은 다음 Research에 포함됩니다.

```bash
python -m scripts.evaluate_ranking_walk_forward \
  --latest 100 --horizon 60 --horizon 240 \
  --scenario-file examples/ranking_scenarios.example.json \
  --initial-research-size 40 --validation-size 10
```

### Robustness statistics

Walk-Forward의 Validation fold/snapshot delta를 대상으로 count, positive/negative/tie, mean/median, min/max/range와 population standard deviation을 설명합니다. p-value, confidence interval, bootstrap이나 자동 의사결정은 계산하지 않습니다.

```bash
python -m scripts.evaluate_ranking_validation_robustness \
  --latest 100 --horizon 60 --horizon 240 \
  --scenario-file examples/ranking_scenarios.example.json \
  --initial-research-size 40 --validation-size 10
```

## Turnover와 cost-adjusted research

### Temporal Turnover

원래 timeline에서 실제로 인접한 `t-1 → t` Top N의 replacement/retention을 계산합니다. Replay failure, baseline signature 또는 Top N 경계는 continuity를 끊고 앞뒤 snapshot을 연결하지 않습니다. Replacement는 selection turnover proxy이며 monetary turnover가 아닙니다.

```bash
python -m scripts.evaluate_temporal_ranking_turnover \
  --latest 100 \
  --scenario-file examples/ranking_scenarios.example.json
```

### Cost-adjusted counterfactual

Gross outcome과 valid turnover transition을 current snapshot으로 정렬합니다. Normalized NAV=1, equal-weight, selection-change-only를 가정하며 sell/buy notional ratio는 각각 canonical replacement rate, gross traded notional은 그 두 배입니다. 첫 snapshot과 continuity break 뒤 첫 snapshot은 synthetic zero cost를 부여하지 않고 제외합니다.

```bash
python -m scripts.evaluate_cost_adjusted_ranking \
  --latest 100 \
  --horizon 60 --horizon 240 --horizon 1440 \
  --scenario-file examples/ranking_scenarios.example.json \
  --fee-rate 0.0005 --spread-cost-rate 0.0005 --slippage-rate 0.001
```

비용률은 traded notional의 Decimal fraction이며 실제 Upbit fee/spread/slippage가 아니라 명시적 research assumption입니다. Retained asset의 가격 drift에 따른 full rebalance나 market impact는 계산하지 않습니다.

Cost-adjusted Walk-Forward와 Robustness는 위 canonical snapshot 결과만 사용합니다.

```bash
python -m scripts.evaluate_cost_adjusted_walk_forward \
  --latest 100 --horizon 60 --horizon 240 --horizon 1440 \
  --scenario-file examples/ranking_scenarios.example.json \
  --fee-rate 0.0005 --spread-cost-rate 0.0005 --slippage-rate 0.001 \
  --initial-research-size 40 --validation-size 10

python -m scripts.evaluate_cost_adjusted_validation_robustness \
  --latest 100 --horizon 60 --horizon 240 --horizon 1440 \
  --scenario-file examples/ranking_scenarios.example.json \
  --fee-rate 0.0005 --spread-cost-rate 0.0005 --slippage-rate 0.001 \
  --initial-research-size 40 --validation-size 10
```

## Candidate generation과 historical screening

### Deterministic Candidate Generator

Reference snapshot의 policy를 검증한 뒤 weight 하나에서 다른 하나로 같은 Decimal step을 옮기는 pairwise 후보를 deterministic하게 만듭니다. Outcome을 보고 winner를 선택하지 않으며 reference parameter와 Top N은 바꾸지 않습니다. 등록된 definition과 중복 여부는 표시하지만 DB에 등록하지 않습니다.

```bash
python -m scripts.generate_ranking_candidates \
  --reference-snapshot-id 123 --step 0.05 --max-candidates 20 \
  --output generated-ranking-candidates.json
```

AI candidate proposal은 현재 구현 범위가 아닙니다.

### Reference-bounded Historical Batch

Reference snapshot `created_at`과 ID/captured/created ceiling 이하의 timeline만 사용해 generator의 novel 후보 전체를 평가합니다. 고정 profile은 gross, turnover, cost-adjusted historical evidence를 만들지만 score, rank, winner를 만들지 않습니다.

```bash
python -m scripts.evaluate_generated_candidate_historical_batch \
  --reference-snapshot-id 123 --step 0.05 \
  --output generated-candidate-historical-batch.json
```

### Historical Screening Gate

Historical Batch 결과를 후보별로 `PASS`, `FAIL`, `INSUFFICIENT`, `INVALID`로 분류합니다. 여러 후보가 PASS할 수 있고 PASS는 LIVE-ready나 자동 registration을 뜻하지 않습니다.

```bash
python -m scripts.evaluate_historical_candidate_screening_gate \
  --reference-snapshot-id 123 --step 0.05 \
  --output historical-candidate-screening.json
```

### Screening-gated registration

Apply 시 과거 JSON을 신뢰하지 않고 Batch와 Screening을 다시 실행합니다. Preview의 exact plan signature를 apply에 요구하며 reference/runtime context나 PASS set이 바뀌면 stale plan을 거부합니다. PASS 후보 전체를 하나의 transaction과 같은 registration anchor로 등록합니다.

```bash
python -m scripts.register_screened_research_candidates \
  --reference-snapshot-id 123 --step 0.05
```

개별 candidate를 사람이 명시적으로 등록할 수도 있습니다. 기본은 dry-run입니다.

```bash
python -m scripts.register_research_policy_candidate \
  --scenario-file examples/ranking_scenarios.example.json \
  --scenario-name research_example_momentum_heavy \
  --reference-snapshot-id 123
```

## Registration-bounded evidence

Candidate registration은 trusted UTC `registered_at`, snapshot ID watermark와 captured-at watermark를 고정합니다.

```text
snapshot.id > registration_snapshot_id_watermark
AND snapshot.captured_at > registered_at
AND snapshot.captured_at > registration_captured_at_watermark
```

### Historical evidence as-of registration

등록 당시 존재하고 알 수 있었던 snapshot/outcome만 재구성합니다. Snapshot에는 ID/captured/created ceiling을, outcome에는 target/evaluated/created/updated as-of를 적용합니다. 등록 후 COMPLETE가 된 과거 outcome은 historical success로 소급하지 않습니다.

```bash
python -m scripts.evaluate_candidate_registration_bounded_historical_evidence \
  --candidate-id <ID> \
  --initial-research-size 2 --validation-size 1 \
  --fee-rate 0.0005 --spread-cost-rate 0.0005 --slippage-rate 0.001
```

이는 candidate 설계에 사용됐을 수 있어 strict unseen evidence가 아닙니다.

### Forward Gross

등록 triple-cutoff 이후 같은 user/exchange/quote/schema/baseline/Top N context만 사용합니다. Horizon별 outcome maturity는 독립적이며 최근 outcome pending은 정상입니다.

```bash
python -m scripts.evaluate_forward_candidate_gross_evidence \
  --candidate-id <ID> --horizon 60 --horizon 240 --horizon 1440
```

### Forward Turnover

Forward broad timeline의 context mismatch와 replay failure를 chronology break로 보존합니다. 첫 Forward snapshot에 pre-registration transition을 만들지 않습니다.

```bash
python -m scripts.evaluate_forward_candidate_turnover_evidence --candidate-id <ID>
```

### Forward Cost-adjusted

Gross와 Turnover의 candidate identity, registration anchor, snapshot set, membership과 signature를 strict하게 정렬합니다. Cost sample은 Gross SUCCESS와 turnover current snapshot의 교집합입니다.

```bash
python -m scripts.evaluate_forward_candidate_cost_adjusted_evidence \
  --candidate-id <ID> --horizon 60 --horizon 240 --horizon 1440 \
  --fee-rate 0.0005 --spread-cost-rate 0.0005 --slippage-rate 0.001
```

Forward evidence는 descriptive evidence입니다. 표본 충분성, promotion eligibility와 human decision은 [Policy Promotion](POLICY_PROMOTION.md)의 별도 단계가 담당합니다.

## Read/write와 external-call 요약

| 기능 | DB write | External call | Runtime/LIVE 영향 |
| --- | --- | --- | --- |
| Replay/A-B/Sweep/Validation/Cost | 없음 | 없음 | 없음 |
| Candidate outcome rebuild dry-run | rollback | Upbit public | 없음 |
| Candidate outcome `--apply` | derived outcome | Upbit public | 없음 |
| Generator/Batch/Screening | 없음 | 없음 | 없음 |
| Registry preview | 없음 | 없음 | 없음 |
| Registry `--apply` | immutable candidate | 없음 | 없음 |
| Historical/Forward evidence | 없음 | 없음 | 없음 |

JSON output을 쓰는 CLI는 명시한 output file만 atomic write하며 기존 파일 교체에는 `--force`가 필요합니다. Repository에 environment-specific candidate ID나 현재 Production row 상태를 문서 contract로 기록하지 않습니다.
