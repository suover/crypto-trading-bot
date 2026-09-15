# 운영 가이드

## Recommendation outcome worker

이 worker는 AI 추천을 수정하거나 주문을 실행하지 않고, persisted recommendation 및
동일 pipeline universe 후보의 1h/4h/24h forward market outcome만 derived table에
기록합니다. `target_at` 당시 완전히 닫힌 Upbit 1분봉만 선택하므로 target minute의
미완성 candle이나 현재 ticker를 과거 가격으로 사용하지 않습니다. COMPLETE는 재사용하고
PARTIAL은 다음 cycle에서 재평가합니다. `RECOMMENDATION_OUTCOME_ENABLED=false`가 안전한
기본값입니다. report CLI는 DB-only이고 rebuild dry-run은 public Upbit 조회를 수행합니다.

Rollout은 migration 및 DB backup 후 disabled 상태를 확인하고, 작은 `--limit` dry-run,
소량 `--apply`, DB/report 검증 순으로 진행합니다. 그 후 운영자가 `.env`를 백업하고 flag를
켜 worker를 recreate한 뒤 로그, 최초 1h outcome, runtime safety를 확인합니다.

## Strategy Replay Dataset

`STRATEGY_REPLAY_DATASET_ENABLED=false`가 기본입니다. 활성화된 market-universe pipeline은
이미 메모리에 수집된 prefilter/HELD 후보와 ranking 결과만 별도 research dataset에
저장하므로 외부 API 호출량과 LIVE/GPT 후보 집합은 변하지 않습니다. Research 저장은
SAVEPOINT로 격리되며 실패 시 오류를 기록하고 기존 universe pipeline은 계속됩니다.
과거 prefilter 탈락 후보는 복원하지 않으며 활성화 이후 데이터만 authoritative합니다.

```bash
python -m scripts.report_strategy_replay_dataset --limit 50
```

Report는 DB-only read입니다. 이 기능은 replay/backtest engine이나 outcome 계산을 수행하지
않습니다.

## Offline Strategy Replay v1

수동 replay CLI는 `StrategyReplaySnapshot`과 연결된 persisted
`StrategyReplayCandidate`만 읽습니다. Snapshot의 historical policy를 복원하고 원래
persisted prefilter pool 안에서 기존 heuristic ranking policy를 호출합니다. DB write,
외부 API, GPT, Telegram, LIVE 변경 및 profitability 평가는 없습니다. 알 수 없는
schema/policy, invalid data, baseline rank/score mismatch는 fail-closed로 보고합니다.

```bash
python -m scripts.replay_strategy_rankings --snapshot-id 123

python -m scripts.replay_strategy_rankings \
  --snapshot-id 123 \
  --override liquidity=0.20 \
  --override trend_alignment=0.20 \
  --override momentum=0.30 \
  --override volume_confirmation=0.10 \
  --override spread=0.08 \
  --override volatility=0.07 \
  --override drawdown=0.05

python -m scripts.replay_strategy_rankings --latest 50 --top-n 5
```

v1은 prefilter/filter/blocklist/universe input, GPT recommendation, sizing 또는 미래
outcome을 변경하거나 재구성하지 못합니다. HELD-only final augmentation은 ranked Top N
비교에서 제외합니다. Batch의 각 snapshot은 자기 stored policy를 baseline으로 사용하고
동일 CLI override를 각 historical baseline에 독립적으로 적용합니다.

## Research Candidate Outcome

Research Candidate Outcome은 persisted Strategy Replay ranking 후보의 1h/4h/24h 기본 horizon
gross market movement를 계산합니다. 기준은 scheduler 시간이 아니라 snapshot
`captured_at`이며 target은 정확히 `captured_at + horizon`입니다. Frozen candidate
`latest_price`를 시작가로 사용하고 current ticker fallback은 하지 않습니다. 종료가는 target
이전에 완전히 닫힌 Upbit 1분봉만 사용하므로 target minute 또는 미래 candle을 보지 않습니다.

기존 `recommendation-outcome-worker`가 Recommendation Outcome과 Research Outcome을 독립된
enabled flag, interval, session/transaction으로 실행합니다. 둘 중 하나만 활성화해도 worker는
동작하고 `--once`는 활성화된 cycle을 각각 한 번 실행합니다. 별도 container나 private Upbit,
OpenAI, Telegram secret/call은 추가하지 않습니다. Public historical request만 증가합니다.

```bash
python -m scripts.rebuild_research_candidate_outcomes --limit 20
python -m scripts.rebuild_research_candidate_outcomes --snapshot-id 123 --apply
python -m scripts.report_research_candidate_outcomes --limit 50
```

기본 rebuild는 public 조회가 가능한 DRY_RUN이며 DB를 rollback합니다. APPLY는 기존 analytics
worker advisory lock을 획득한 경우에만 derived row를 upsert합니다. COMPLETE는 재조회하지 않고
historical price unavailable PARTIAL만 재시도합니다. Report는 DB-only입니다. 이 데이터는
actual trading PnL이나 아직 구현하지 않은 Strategy A/B aggregate 성과가 아닙니다.

## Strategy A/B Performance Evaluation v1

이 manual CLI는 Offline Strategy Replay의 baseline/scenario TopN과 저장된 Research Candidate
Outcome을 연결하는 DB-only/read-only 분석입니다. 동일 snapshot, candidate pool, stored TopN을
유지하고 ranking weights만 변경합니다. 결과 유형은
`RANKING_SELECTION_GROSS_MARKET_PERFORMANCE`이며 수수료, slippage, 실제 주문, GPT selection,
sizing이 없는 equal-weight hypothetical gross return입니다.

```bash
python -m scripts.evaluate_strategy_ab_performance \
  --snapshot-id 2 \
  --horizon 240 \
  --override liquidity=0.20 \
  --override trend_alignment=0.20 \
  --override momentum=0.30 \
  --override volume_confirmation=0.10 \
  --override spread=0.08 \
  --override volatility=0.07 \
  --override drawdown=0.05

python -m scripts.evaluate_strategy_ab_performance \
  --latest 100 \
  --horizon 240 \
  --override liquidity=0.20 \
  --override trend_alignment=0.20 \
  --override momentum=0.30 \
  --override volume_confirmation=0.10 \
  --override spread=0.08 \
  --override volatility=0.07 \
  --override drawdown=0.05
```

Status 의미:

- `SUCCESS`: baseline integrity와 양쪽 TopN COMPLETE coverage가 모두 확인되어 계산됨
- `BASELINE_INTEGRITY_FAILED`: stored baseline ranking을 정확히 재현하지 못함
- `REPLAY_INCOMPATIBLE`: schema/policy/source replay가 호환되지 않음
- `OUTCOME_INCOMPLETE`: requested horizon의 selected outcome이 없거나 PARTIAL
- `INVALID_OUTCOME_DATA`: COMPLETE row의 return/lineage/중복이 비정상

Partial TopN 평균은 계산하지 않고 batch aggregate는 SUCCESS snapshot만 사용합니다. 출력은
descriptive analytics일 뿐 통계적 유의성이나 LIVE 정책 우월성을 의미하지 않습니다. 표본이 적을
때 LIVE ranking 변경의 근거로 사용하지 않습니다. CLI는 DB write/commit이나 Upbit/OpenAI/
Telegram/CoinGecko/Fear & Greed 호출을 수행하지 않습니다.

## Ranking Scenario Sweep / Research Runner v1

이 manual runner는 명시적 JSON scenario의 component weights만 기존 Strategy A/B evaluator에
전달합니다. Reference parameter와 stored TopN은 각 historical snapshot 값을 상속합니다.
Grid/range/random 조합, optimizer, 자동 scenario 생성, 자동 winner 또는 LIVE policy 적용은
지원하지 않습니다. DB에 저장된 replay dataset과 candidate outcome만 읽으며 write/commit과
Upbit/OpenAI/Telegram/CoinGecko/Fear & Greed 호출은 없습니다.

```bash
python -m scripts.evaluate_ranking_scenario_sweep \
  --latest 100 \
  --horizon 60 \
  --horizon 240 \
  --scenario-file examples/ranking_scenarios.example.json

python -m scripts.evaluate_ranking_scenario_sweep \
  --snapshot-id 2 \
  --horizon 240 \
  --scenario-file examples/ranking_scenarios.example.json
```

둘 다 생략하면 `--latest 1`이며 `--snapshot-id`와 `--latest`는 동시에 사용할 수 없습니다.
`--horizon`은 하나 이상 반복할 수 있는 양의 정수이고 중복은 거부합니다. Scenario 파일의
schema version은 `ranking-scenario-sweep-v1`입니다. Component field는 현재 ranking component
exact set, 값은 finite/nonnegative Decimal, 합은 정확히 1이어야 하며 reference field나 unknown
field는 거부합니다. 이름과 weight vector도 중복될 수 없습니다.

출력은 먼저 scenario 정의/signature를 파일 순서대로 표시한 뒤 horizon/cohort 결과를 표시합니다.
각 cohort는 같은 `baseline_policy_signature`와 `effective_top_n`만 포함합니다.
`candidate_snapshot_count`는 raw 대상 수,
`common_comparable_snapshot_count`는 모든 scenario가 동시에 SUCCESS인 교집합 크기,
`common_coverage_rate`는 둘의 비율입니다. Scenario별 `raw_*_count`는 common set에서 제외된
원인을 보여주고, win/loss/tie, win rate, baseline/scenario mean, mean delta, snapshot delta
median, positive rate는 common set에서만 재계산됩니다. `NO_COMMON_COMPARABLE_SNAPSHOTS`이면
`performance_compared=false`이고 metric은 비어 있습니다. Snapshot metadata나 baseline metric
alignment가 깨지면 `INVALID_SWEEP_DATA`와 exit code 1로 fail-closed합니다. 정상 report는 일부
incomplete가 있어도 exit code 0이며 입력 오류는 2, fatal 오류는 1입니다.

Horizon마다 common set과 cohort가 독립적이며 이를 하나의 cross-horizon composite로 합치지
않습니다. 예제 `research_example_*` scenario는 입력 형식을 위한 연구 예시이며 추천값이
아닙니다. 많은 scenario를 같은 historical data에서 반복하면 우연한 성과를 선택하는
overfitting 위험이 커집니다. 결과만으로 LIVE weight를 변경하지 말고 충분한 snapshot과 별도
out-of-sample/holdout 검증을 거쳐야 합니다. 이 결과는 ranking-selection gross market movement의
기술 통계이지 실제 trading PnL, 통계적 유의성, 미래 수익 보장 또는 자동 LIVE recommendation이
아닙니다.

## Temporal Ranking Holdout Validation v1

이 manual CLI는 Ranking Scenario Sweep과 동일한 scenario execution, cohort integrity 및 common
SUCCESS intersection을 사용한 뒤 common snapshot만 시간순으로 나눕니다. Horizon,
`baseline_policy_signature`, `effective_top_n`이 다른 데이터는 서로 섞지 않습니다. 정렬은 항상
`captured_at ASC, snapshot_id ASC`이고 random split은 지원하지 않습니다. Research와 Holdout
성과는 각각 기존 Strategy A/B `summarize_results()`로 계산합니다.

기본 ratio split:

```bash
python -m scripts.evaluate_ranking_holdout_validation \
  --latest 100 \
  --horizon 60 \
  --horizon 240 \
  --scenario-file examples/ranking_scenarios.example.json
```

명시적 ratio와 fixed cutoff:

```bash
python -m scripts.evaluate_ranking_holdout_validation \
  --latest 100 \
  --horizon 240 \
  --scenario-file examples/ranking_scenarios.example.json \
  --holdout-ratio 0.25

python -m scripts.evaluate_ranking_holdout_validation \
  --latest 100 \
  --horizon 240 \
  --scenario-file examples/ranking_scenarios.example.json \
  --research-cutoff-at 2026-09-01T00:00:00+09:00
```

Ratio 기본값은 `0.30`이고 `0 < ratio < 1`이어야 합니다.
`holdout_count = ceil(common_count * ratio)` 후, common snapshot이 최소 2개이면 양쪽에 최소 1개가
남도록 제한합니다. Cutoff는 timezone offset을 포함해야 하며 UTC로 canonicalize합니다.
`captured_at <= cutoff`는 Research, 그보다 늦으면 Holdout입니다. Ratio와 cutoff는 동시에 사용할
수 없습니다.

Report의 `TEMPORAL_RATIO`는 retrospective split이고 `strict_unseen_holdout=false`입니다.
`FIXED_TEMPORAL_CUTOFF`도 future-data non-disclosure를 기술적으로 증명하지 않으므로
`strict_unseen_holdout=not_verified`입니다. Common snapshot이 없거나 양쪽 split이 불가능하면
각각 `NO_COMMON_COMPARABLE_SNAPSHOTS`, `INSUFFICIENT_TEMPORAL_SPLIT_DATA`로 exit 0 safe report를
생성하며 성과 metric은 비웁니다. Sweep integrity 위반은 `INVALID_HOLDOUT_DATA`와 exit 1입니다.
입력 오류는 exit 2입니다.

이 CLI는 DB-only/read-only이며 external call, worker, schedule, DB write 또는 LIVE policy 변경이
없습니다. `policy_decision_performed=false`이고 자동 winner/recommendation/promotion을 출력하지
않습니다. 실제 trading PnL이나 통계적 검증이 아니며, 작은 표본만으로 정책 결정을 내려서는 안
됩니다. Ratio와 fixed cutoff 모두 unseen 보장은 사용자의 scenario 설계 및 연구 프로세스까지
통제해야 합니다.

## Ranking Walk-Forward Validation v1

하나의 Holdout 대신 여러 non-overlapping Validation fold에서 고정 explicit scenarios를
반복 평가합니다. 이는 scenario 재학습이나 nested model selection이 아니라 expanding-window
repeated temporal validation입니다. 공통 matrix가 horizon과
`(baseline_policy_signature, effective_top_n)`별 cohort 및 모든 scenario의 `SUCCESS`
교집합을 만들고, Walk-Forward는 이를 `captured_at ASC, snapshot_id ASC`로만 정렬합니다.

```bash
python -m scripts.evaluate_ranking_walk_forward \
  --latest 100 \
  --horizon 60 \
  --horizon 240 \
  --scenario-file examples/ranking_scenarios.example.json \
  --initial-research-size 40 \
  --validation-size 10
```

Fold `k`의 Research 끝은 `initial_research_size + k * validation_size`이고, 바로 다음
`validation_size`개 snapshot이 Validation입니다. Step도 Validation 크기로 고정되어 Validation
구간이 겹치지 않으며 이전 Validation은 다음 expanding Research에 포함됩니다. 불완전한 마지막
구간은 평가하지 않고 `unused_tail_snapshot_count`로 보고합니다. 최소 한 full fold가 없으면
`INSUFFICIENT_WALK_FORWARD_DATA`, common set이 비면
`NO_COMMON_COMPARABLE_SNAPSHOTS`로 exit 0입니다. Matrix/chronology/subset integrity 위반은
`INVALID_WALK_FORWARD_DATA`로 exit 1, 입력 오류는 exit 2입니다.

각 subset은 기존 Strategy A/B `summarize_results()`만 사용합니다. 이 명령은 DB SELECT만
수행하고 외부 API, DB write, worker, LIVE policy를 건드리지 않습니다. 결과는 실제 trading
PnL이나 통계적 유의성이 아니고 자동 winner/recommendation/promotion도 생성하지 않습니다.
또한 scenario 작성자가 미래 데이터를 보지 않았음을 증명하지 못하므로
`strict_unseen_validation=not_verified`입니다. 작은 표본으로 운영 정책을 결정하지 마십시오.

## Ranking Validation Robustness Statistics v1

Walk-Forward 결과의 Validation fold와 개별 Validation snapshot만 분석하는 manual research
CLI입니다. 같은 scenario evaluation matrix를 한 번만 만들고 기존 Walk-Forward fold를
재사용하므로 Research snapshot, unused tail, non-common 또는 non-SUCCESS 결과는 통계에
포함하지 않습니다.

```bash
python -m scripts.evaluate_ranking_validation_robustness \
  --latest 100 \
  --horizon 60 \
  --horizon 240 \
  --scenario-file examples/ranking_scenarios.example.json \
  --initial-research-size 40 \
  --validation-size 10
```

Fold/snapshot별 count, positive rate, mean/median, min/max/range, worst/best 및 Decimal population
standard deviation을 출력합니다. 이는 observed 값의 deterministic descriptive statistics이며
통계적 유의성이나 독립표본 inference가 아닙니다. p-value, confidence interval, bootstrap,
threshold, 자동 winner 또는 promotion은 없습니다.

Common set이 없으면 `NO_COMMON_COMPARABLE_SNAPSHOTS`, full fold가 없으면
`INSUFFICIENT_WALK_FORWARD_DATA`로 exit 0입니다. 중복 Validation ID, 누락·non-SUCCESS result,
잘못된 metadata/delta 또는 upstream invalid 상태는 `INVALID_ROBUSTNESS_DATA`로 exit 1이며 입력
오류는 exit 2입니다. 이 명령은 DB SELECT만 사용하고 worker, scheduler, migration, external API,
DB write, LIVE policy 변경을 수행하지 않습니다. `strict_unseen_validation=not_verified`이며 작은
표본으로 운영 결론을 내려서는 안 됩니다.

## Temporal Ranking Turnover / Rebalance Research v1

Offline Strategy Replay 결과만 이용해 시간에 따른 TopN selection replacement/retention을
확인하는 manual CLI입니다. Candidate outcome과 horizon은 필요하지 않습니다.

```bash
python -m scripts.evaluate_temporal_ranking_turnover \
  --latest 100 \
  --scenario-file examples/ranking_scenarios.example.json
```

시간 순서는 `captured_at UTC ASC, snapshot_id ASC`이며 원래 sequence의 인접 위치만 비교합니다.
모든 scenario가 replay와 baseline integrity를 통과하지 못한 위치, baseline policy signature 변경,
effective TopN 변경은 continuity break입니다. 실패 위치를 제거한 뒤 앞뒤 snapshot을 연결하지
않습니다.

`NO_REPLAY_SNAPSHOTS`, `NO_COMMON_REPLAYABLE_SNAPSHOTS`,
`INSUFFICIENT_TEMPORAL_TRANSITIONS`는 안전한 연구 결과로 exit 0이고,
`INVALID_TURNOVER_DATA`는 integrity failure로 exit 1, 입력 오류는 exit 2입니다. 이 명령은 DB
SELECT only이며 migration, worker, scheduler, external API, DB write 또는 LIVE side effect가
없습니다. 결과는 counterfactual TopN selection proxy이고 monetary rebalance, 비용, 실제 거래나
성과를 뜻하지 않으며 자동 winner/promotion도 수행하지 않습니다.

## Cost-adjusted Ranking Counterfactual Evaluation v1

Temporal TopN transition과 horizon별 Strategy A/B gross outcome을 같은 current snapshot lineage로
정렬하는 manual, DB SELECT-only research CLI입니다.

```bash
python -m scripts.evaluate_cost_adjusted_ranking \
  --latest 100 \
  --horizon 60 \
  --horizon 240 \
  --horizon 1440 \
  --scenario-file examples/ranking_scenarios.example.json \
  --fee-rate 0.0005 \
  --spread-cost-rate 0.0005 \
  --slippage-rate 0.001
```

비용률은 traded notional의 fraction이며 모두 명시적으로 입력합니다. Normalized NAV=1,
equal-weight, selection-change-only 모델이므로 retained asset drift나 실제 KRW notional을 사용하지
않습니다. Cost ratio는 100을 곱해 percentage-point 단위로 변환한 뒤 Baseline과 Scenario gross
return 양쪽에서 차감합니다. 저장된 spread는 execution cost로 사용하지 않습니다.

`NO_TURNOVER_TRANSITIONS`, `NO_COMMON_COMPARABLE_SNAPSHOTS`,
`NO_COST_ADJUSTABLE_SNAPSHOTS`는 안전한 연구 결과로 exit 0,
`INVALID_COST_ADJUSTED_DATA`는 integrity failure로 exit 1, 입력 오류는 exit 2입니다. 별도 env,
migration, worker, scheduler 또는 Compose service가 없으며 외부 API와 LIVE side effect도 없습니다.
결과는 실제 PnL이나 실제 주문 simulation이 아니고 자동 winner/promotion을 수행하지 않습니다.

## Cost-adjusted Walk-Forward Validation v1

Cost-adjusted evaluator를 command당 한 번 실행한 뒤, 그 결과의 cost-adjustable snapshot만 메모리에서
expanding-window Research/Validation fold로 나눕니다. Gross common snapshot이나 비용 계산을 다시
구성하지 않으며 Validation 첫 snapshot의 기존 transition 비용도 초기화하지 않습니다.

```bash
python -m scripts.evaluate_cost_adjusted_walk_forward \
  --latest 100 \
  --horizon 60 \
  --horizon 240 \
  --horizon 1440 \
  --scenario-file examples/ranking_scenarios.example.json \
  --fee-rate 0.0005 \
  --spread-cost-rate 0.0005 \
  --slippage-rate 0.001 \
  --initial-research-size 40 \
  --validation-size 10
```

Validation fold는 서로 겹치지 않고 partial tail은 제외합니다. 같은 fold의 gross delta와
cost-adjusted delta를 함께 출력하지만 automatic winner, promotion 또는 policy decision은
수행하지 않으며 `strict_unseen_validation=not_verified`입니다.

`SUCCESS`는 full Validation fold가 하나 이상인 경우입니다.
`NO_COST_ADJUSTABLE_SNAPSHOTS`와
`INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA`는 exit 0의 안전한 연구 결과이고,
`INVALID_COST_ADJUSTED_WALK_FORWARD_DATA`는 exit 1, 잘못된 CLI 입력은 exit 2입니다.

이 명령은 manual CLI이며 DB SELECT-only입니다. Cost-adjusted evaluation은 한 번만 수행되고 fold
검증은 in-memory입니다. 별도 worker, scheduler, migration, 설정, 외부 API 또는 LIVE side effect가
없습니다.

## Cost-adjusted Validation Robustness Statistics v1

Cost-adjusted Walk-Forward의 Validation fold와 해당 fold에 실제 포함된 snapshot만 대상으로
deterministic descriptive statistics를 생성합니다. 한 command 안에서 Cost-adjusted evaluation을
정확히 한 번 실행한 뒤, 동일한 immutable result를 Walk-Forward에 전달하고, 다시 그 source result와
Walk-Forward result를 robustness evaluator에 함께 전달합니다. 비용·turnover·ranking·gross return은
재계산하지 않습니다.

```bash
python -m scripts.evaluate_cost_adjusted_validation_robustness \
  --latest 100 \
  --horizon 60 \
  --horizon 240 \
  --horizon 1440 \
  --scenario-file examples/ranking_scenarios.example.json \
  --fee-rate 0.0005 \
  --spread-cost-rate 0.0005 \
  --slippage-rate 0.001 \
  --initial-research-size 40 \
  --validation-size 10
```

Fold 분포에는 `validation.mean_cost_adjusted_return_delta`, snapshot 분포에는 source의
`cost_adjusted_return_delta`를 사용합니다. 순서는 `captured_at UTC ASC, snapshot_id ASC`이며
Research snapshot, incomplete tail, 중복 Validation ID는 포함하지 않습니다. 표준편차는 관찰값의
population standard deviation입니다. count 1도 정상이며 표본 충분성이나 통계적 유의성을 판단하지
않습니다.

`SUCCESS`, `NO_COST_ADJUSTABLE_SNAPSHOTS`,
`INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA`는 exit 0이고,
`INVALID_COST_ADJUSTED_ROBUSTNESS_DATA`는 exit 1, 입력 오류는 exit 2입니다. 실행은 DB SELECT-only,
in-memory report이며 DB write, worker, scheduler, migration, external API 또는 LIVE side effect가
없습니다. 자동 winner, promotion, policy decision도 수행하지 않습니다.

## Research Policy Candidate Registry v1

Forward-only 연구를 시작할 explicit Ranking Scenario 하나를 reference Replay Snapshot context에
수동 등록합니다. Scenario parser와 baseline replay policy parser를 그대로 사용하며 raw weight,
user, exchange, quote asset, TopN 또는 등록시각을 CLI에서 덮어쓸 수 없습니다.

먼저 dry-run으로 validation과 watermark 계획을 확인합니다. 이 단계는 rollback되며 candidate ID나
영구 registered time을 생성하지 않습니다.

```bash
python -m scripts.register_research_policy_candidate \
  --scenario-file examples/ranking_scenarios.example.json \
  --scenario-name research_example_momentum_heavy \
  --reference-snapshot-id 8
```

명시적으로 `--apply`한 경우에만 `research_policy_candidates`에 insert하고 commit합니다.

```bash
python -m scripts.register_research_policy_candidate \
  --scenario-file examples/ranking_scenarios.example.json \
  --scenario-name research_example_momentum_heavy \
  --reference-snapshot-id 8 \
  --apply
```

`CREATED`는 새 immutable anchor가 저장됐다는 뜻입니다. `ALREADY_REGISTERED`는 같은 research
context와 definition의 최초 row를 그대로 반환한 것으로, 이후 Replay Snapshot이 추가됐더라도
registered time과 두 watermark를 갱신하지 않습니다. 이름/definition alias conflict와 reference
snapshot integrity failure는 exit 1, scenario/CLI 입력 오류는 exit 2입니다.

등록 anchor는 trusted UTC `registered_at`, matching context의 최대 snapshot ID, 최대 captured-at을
각각 고정합니다. 향후 별도 Forward Validation은 다음 조건을 모두 적용해야 합니다.

```text
snapshot.id > registration_snapshot_id_watermark
AND snapshot.captured_at > registered_at
AND snapshot.captured_at > registration_captured_at_watermark
```

현재 command는 anchor 등록만 수행합니다. Forward evidence 생성, promotion, shadow/LIVE policy
적용은 없으며 Upbit/OpenAI/Telegram 등 외부 호출도 없습니다. Worker, scheduler, Compose service,
env flag를 추가하지 않습니다.

## Forward-only Candidate Gross Evidence v1

등록된 candidate의 frozen DB definition을 사용해 registration anchor 이후 snapshot만 수동
평가합니다. Scenario file이나 weight override를 받지 않습니다.

```bash
python -m scripts.evaluate_forward_candidate_gross_evidence \
  --candidate-id 1 \
  --horizon 60 \
  --horizon 240 \
  --horizon 1440
```

실행 전 candidate schema, stored component weights와 definition signature, reference snapshot의
user/exchange/quote/dataset/baseline signature/TopN lineage를 다시 검증합니다. Forward snapshot은
동일 baseline context이면서 다음 triple-cutoff를 모두 만족해야 합니다.

```text
snapshot.id > registration_snapshot_id_watermark
AND snapshot.captured_at > registered_at
AND snapshot.captured_at > registration_captured_at_watermark
```

Candidate 등록 직후에는 `NO_FORWARD_SNAPSHOTS`가 정상입니다. Eligible snapshot은 있지만 해당
horizon outcome이 아직 완성되지 않았다면 `FORWARD_OUTCOMES_PENDING`도 정상입니다. 한 horizon의
pending은 다른 horizon의 `SUCCESS` 결과를 제거하지 않습니다.

기존 Offline Replay 및 Strategy A/B gross 결과를 사용하며 pending/incompatible/invalid snapshot을
0 수익률로 평균에 넣지 않습니다. 명령은 DB SELECT-only이고 외부 API, write, worker, scheduler,
migration 또는 LIVE policy 변경이 없습니다. Cost-adjusted evidence와 promotion은 수행하지 않습니다.

## Forward-only Candidate Turnover Evidence v1

등록된 candidate의 frozen DB definition을 사용해 registration 이후 Forward TopN 교체율을 수동으로
평가합니다. Scenario file, horizon, latest 인자를 받지 않습니다.

```bash
python -m scripts.evaluate_forward_candidate_turnover_evidence --candidate-id 1
```

동일 user/exchange/quote/dataset에서 registration triple-cutoff를 strict `>`로 통과한 broad timeline 전체를
Offline Replay와 Temporal Ranking Turnover에 전달합니다. 중간 baseline policy/TopN 변경, baseline mismatch,
replay incompatibility는 continuity break이며 앞뒤 snapshot을 bridge하지 않습니다. Pre-registration에서
첫 Forward snapshot으로의 transition은 없고, Forward snapshot이 하나뿐이면
`INSUFFICIENT_FORWARD_TRANSITIONS`가 정상입니다.

명령은 DB SELECT-only이며 outcome/cost data, 외부 API, worker, scheduler, Compose service, env flag,
migration 또는 LIVE policy를 사용하거나 변경하지 않습니다. Forward Cost-adjusted Evidence와 promotion은
별도 후속 단계입니다.

## Forward-only Candidate Cost-adjusted Evidence v1

등록된 candidate의 Forward Gross와 Forward Turnover를 strict하게 정렬한 뒤 기존 Historical canonical
selection-change cost model을 적용합니다. Cost assumptions는 implicit default 없이 매번 명시합니다.

```bash
python -m scripts.evaluate_forward_candidate_cost_adjusted_evidence \
  --candidate-id 1 \
  --horizon 60 \
  --horizon 240 \
  --horizon 1440 \
  --fee-rate 0.0005 \
  --spread-cost-rate 0.0005 \
  --slippage-rate 0.001
```

Cost-adjustable 표본은 Gross `SUCCESS`와 Turnover current snapshot의 교집합입니다. 첫 Forward snapshot,
pre-registration transition 및 continuity break를 건너뛴 pair에는 비용 row를 만들지 않습니다. 최근
outcome이 아직 완성되지 않았거나 Forward transition이 부족하면 `FORWARD_OUTCOMES_PENDING`,
`INSUFFICIENT_FORWARD_TRANSITIONS`, `NO_FORWARD_COST_ADJUSTABLE_SNAPSHOTS`는 정상 safe state입니다.

명령은 DB SELECT-only이고 external API, LIVE policy, worker, scheduler, Compose service, env flag 또는
migration을 변경하지 않습니다. Sample sufficiency, statistical inference 및 promotion도 수행하지 않습니다.

## Candidate Registration-Bounded Historical Evidence v1

등록 Candidate의 trusted anchor 시점에 실제 이용 가능했던 Historical evidence만 재구성합니다.

```bash
python -m scripts.evaluate_candidate_registration_bounded_historical_evidence \
  --candidate-id 1 \
  --horizon 60 \
  --horizon 240 \
  --horizon 1440 \
  --initial-research-size 2 \
  --validation-size 1 \
  --fee-rate 0.0005 \
  --spread-cost-rate 0.0005 \
  --slippage-rate 0.001
```

범위는 Candidate `registered_at`과 두 registration watermark가 정하므로 `--latest`, `--as-of`,
scenario file 입력은 없습니다. Snapshot `created_at`까지 상한을 적용하며 outcome timestamp 조건은
outer join의 ON 절에 적용되어 cutoff 이후 outcome이 Candidate row 자체를 제거하지 않고
`OUTCOME_INCOMPLETE`로 보이게 합니다.

등록 전 snapshot이라도 해당 horizon outcome이 Candidate 등록 후에야 COMPLETE되었다면 bounded
Historical success evidence에는 포함되지 않습니다. 이 계층은 strict unseen 검증이 아니며 실제 unseen
검증은 Forward evidence가 담당합니다. DB SELECT-only이고 외부 API, worker, migration, promotion 및
LIVE 정책 변경은 없습니다. 다음 별도 연구 단계는 `Policy Promotion Gate v1`입니다.

이 문서는 서버에서 `crypto-trading-bot`을 반복 가능하고 안전하게 운영하기 위한 절차를 정리합니다. 서버 기준 프로젝트 경로는 다음과 같습니다.

```bash
/home/ubuntu/apps/crypto-trading-bot
```

현재 scheduled approval-based Production LIVE 운영과 GitHub Actions CD 절차는 [Production LIVE 운영 문서](PRODUCTION_LIVE.md)를 우선 참고합니다. [제한적 LIVE 런북](LIVE_RUNBOOK.md)은 첫 5,000 KRW 검증 절차로 계속 보존됩니다.

## 서버 프로젝트 경로로 이동

```bash
cd /home/ubuntu/apps/crypto-trading-bot
```

## Git 상태 확인

운영 작업 전에는 현재 변경 사항을 먼저 확인합니다.

```bash
git status --short
```

의도하지 않은 변경 사항이 있으면 배포나 재시작 전에 내용을 확인합니다.

## 최신 main 가져오기

현재 브랜치와 원격 상태를 확인한 뒤 main을 최신화합니다.

```bash
git branch --show-current
git fetch origin main
git merge --ff-only origin/main
```

운영 서버에서 직접 수정한 파일이 있다면 `git pull` 전에 반드시 내용을 확인합니다. 특히 `.env`는 Git에 포함하지 않습니다.

## 서버 비밀 파일 준비

서버 비밀 파일은 `/etc/crypto-trading-bot/secrets`에 준비합니다.

```bash
sudo mkdir -p /etc/crypto-trading-bot/secrets
sudo chmod 700 /etc/crypto-trading-bot/secrets

sudo touch /etc/crypto-trading-bot/secrets/postgres_password
sudo touch /etc/crypto-trading-bot/secrets/openai_api_key
sudo touch /etc/crypto-trading-bot/secrets/telegram_bot_token
sudo touch /etc/crypto-trading-bot/secrets/upbit_access_key
sudo touch /etc/crypto-trading-bot/secrets/upbit_secret_key

sudo chmod 600 /etc/crypto-trading-bot/secrets/*
```

각 파일에는 비밀값만 저장하며 `KEY=`, 따옴표, 주석을 넣지 않습니다. 비밀 디렉터리는 Git에 포함하지 않습니다. 서버 `.env`에는 다음 경로와 기존 비밀이 아닌 런타임 설정을 유지합니다.

```env
SECRET_DIR=/etc/crypto-trading-bot/secrets
```

컨테이너 시작 전에 필수 비밀 파일이 모두 존재하는지 확인합니다.

> **주의:** 기존 PostgreSQL 데이터 볼륨을 사용한다면 `postgres_password`에는 기존 데이터베이스 역할이 현재 사용하는 암호를 먼저 저장해야 합니다. 파일만 변경해도 기존 역할 암호는 회전되지 않습니다.

## Docker Compose 설정 확인

컨테이너를 재생성하기 전에 Compose 설정을 확인합니다.

```bash
docker compose config --quiet
```

수동 AI 분석 profile까지 포함해 확인하려면 다음 명령을 사용할 수 있습니다.

```bash
docker compose --profile manual config --quiet
```

두 명령은 해석된 환경값을 출력하지 않고 설정의 유효성을 검사합니다.

## 기본 런타임 시작 또는 재생성

서버/프로덕션 유사 기본 런타임은 다음 명령으로 시작하거나 재생성합니다.

```bash
docker compose up -d --build
```

기본 런타임은 다음 서비스를 대상으로 합니다.

- `postgres`
- `migrate`
- `telegram-listener`
- `mock-order-retry-worker`
- `live-order-reconciliation-worker`
- `bot-trading-pnl-worker`

`ai-trade-scheduler`는 기본 런타임에서 시작되지 않습니다.

## 컨테이너 상태 확인

```bash
docker compose ps -a
```

`postgres`, `telegram-listener`, `mock-order-retry-worker`, `live-order-reconciliation-worker`, `bot-trading-pnl-worker`가 실행 중인지 확인합니다. `migrate`는 정상적으로 완료된 뒤 종료될 수 있습니다.

`bot-trading-pnl-worker`도 기본 runtime에 포함되지만 `BOT_TRADING_PNL_ENABLED=false` 기본값에서는 DB accounting query/write 없이 대기합니다.

## 로그 확인

마이그레이션 로그:

```bash
docker compose logs --tail=100 migrate
```

Telegram listener 로그:

```bash
docker compose logs --tail=100 telegram-listener
```

모의 주문 재시도 워커 로그:

```bash
docker compose logs --tail=100 mock-order-retry-worker
```

기존 LIVE 주문 상태 추적 로그:

```bash
docker compose logs --tail=100 live-order-reconciliation-worker
```

Bot PnL accounting 로그:

```bash
docker compose logs --tail=100 bot-trading-pnl-worker
```

LIVE execution ledger에서 `amount_krw`/`quantity`/`price`는 주문 요청값이고, `executed_quantity`/`executed_funds_krw`/`average_execution_price`/`paid_fee`는 Upbit 응답에서 정규화한 실제 체결값입니다. 개별 `trades[]`는 `order_fills`에 저장되고 원본 `raw_response`는 유지됩니다. `execution_synced_at`은 거래소 체결시각이 아니라 마지막 DB 동기화 시각입니다.

과거 저장 JSON을 점검할 때는 먼저 dry-run만 실행합니다. 이 명령은 네트워크를 사용하지 않고 `LIVE`·`UPBIT`의 `raw_response.order_status_response`만 읽습니다. `--apply`는 대상 DB를 재확인한 뒤 별도 승인된 작업에서만 사용합니다.

```bash
python -m scripts.backfill_live_execution_ledger
python -m scripts.backfill_live_execution_ledger --apply
```

## Bot Trading PnL accounting

Portfolio snapshot은 수동 거래와 입출금을 포함할 수 있는 실제 계좌 전체 상태입니다. Bot Trading PnL은 이와 별개로 이 프로그램이 만든 terminal LIVE 주문의 정규화된 체결 summary만 사용합니다. `LIVE_DONE`/`LIVE_EXECUTED_CANCELLED`만 source이고, BUY fee는 FIFO lot 원가에 포함하며 SELL fee는 proceeds에서 차감합니다.

봇 BUY lot이 없는 SELL은 `UNMATCHED`, 일부 수량만 연결되면 `PARTIALLY_MATCHED`입니다. 해당 수량의 원가를 계좌 평단이나 Portfolio에서 추정하지 않습니다. 불완전 terminal source가 발견되면 같은 market의 이후 chronology는 fail-closed로 계산하지 않고 summary를 `PARTIAL`로 둡니다. 다른 market은 계속 계산합니다.

점검은 항상 dry-run부터 시작합니다. 기본 명령은 SELECT와 normalization만 수행하며 INSERT/UPDATE/DELETE/flush/commit/row lock을 하지 않습니다. Production `--apply`는 별도 승인된 운영 작업으로 취급합니다.

```bash
python -m scripts.rebuild_bot_trading_pnl
python -m scripts.rebuild_bot_trading_pnl --apply
python -m scripts.run_bot_trading_pnl_worker --once
```

worker는 DB-only이며 주문 생성/조회/취소, Upbit, AI, Telegram 호출이 없습니다. `BOT_TRADING_PNL_ENABLED=false`가 rollout-safe 기본이고 interval 기본값은 300초입니다.

## Portfolio Performance accounting

계좌 성과는 `COMPLETE PortfolioSnapshot`과 완료된 Account Activity 입출금을 사용합니다.
ORDER는 제외하고, crypto flow는 event 시각 이전에 완전히 종료된 Upbit 1분봉 종가로
평가합니다. 여기서 event 시각은 `created_at/occurred_at`이 아니라 완료 `done_at/completed_at`입니다.
Period inclusion은 `(previous_snapshot, current_snapshot]`입니다. Deposit과 withdrawal coverage 또는 가격이 불완전하면 해당 period와 이후
cumulative chain은 `PARTIAL`입니다.

Modified Dietz period return을 100 기준 performance index로 연결하므로 외부 입출금이
high-water mark/drawdown/MDD를 직접 왜곡하지 않습니다. 먼저 dry-run을 확인하고 운영 반영은
별도 승인과 백업 후에만 `--apply`로 수행합니다.
KRW high-water mark/drawdown은 최초 COMPLETE NAV에 index를 적용한 조정값이며 raw NAV가
아닙니다.

```bash
python -m scripts.rebuild_portfolio_performance
python -m scripts.rebuild_portfolio_performance --apply
docker compose logs --tail=100 portfolio-performance-worker
```

`PORTFOLIO_PERFORMANCE_ENABLED=false`가 배포 기본값입니다. Worker는 PostgreSQL secret만
mount하며 Upbit private key, OpenAI, Telegram secret을 받지 않습니다.

AI 분석 pipeline은 AccountSnapshot → Market Universe → Portfolio valuation → AI recommendation → Telegram 순서로 실행됩니다. Portfolio 단계는 동일 `CRYPTO_TRADING_PIPELINE_RUN_ID`의 DB 가격을 우선합니다. Upbit 가격이 없는 보유자산은 `PORTFOLIO_COINGECKO_ASSET_MAPPING`에 명시적인 `ASSET=coingecko-id`가 있는 경우에만 CoinGecko KRW 현재가를 조회합니다. 기본 매핑은 비어 있고 symbol 자동 추론은 하지 않으며, 누락·비정상·요청 실패는 0원이 아닌 `UNPRICED`로 유지합니다. 새 계좌 수집은 `ACCOUNT_SNAPSHOT` run type을 사용하고 legacy `MANUAL` account run도 조회 호환됩니다.

`PORTFOLIO_EXCLUDED_ASSETS`는 기본값이 빈 explicit policy입니다. 여기에 포함된 currency는
AccountSnapshot과 PortfolioPosition audit row를 유지하되 position을
`EXCLUDED`/`POLICY_EXCLUDED`로 기록하고 NAV, unpriced count, aggregate cost basis/PnL 및
CoinGecko 요청에서 제외합니다. Market Universe의 ranked/static/held 후보와 신규 LIVE 주문도
차단합니다. 이는 cash-flow나 손실이 아니며 Account Activity와 Bot FIFO/PnL을 변경하지
않습니다. exclusion signature가 연속 COMPLETE snapshot 사이에서 달라지면 Performance는
정책 차이를 수익률로 계산하지 않고 새 index-100 baseline을 만듭니다.

`COMPLETE`는 KRW와 정책상 포함된 모든 양수 보유자산의 가격을 확보했다는 뜻입니다. `PARTIAL`의 `known_total_value_krw`는 알려진 범위만 합한 값이며 전체 계좌 총자산이 아닙니다. `total_value_krw`는 PARTIAL에서 NULL입니다. 가격은 모두 있어도 포함자산 cost basis가 하나라도 없으면 valuation은 COMPLETE일 수 있지만 aggregate cost basis와 미실현손익은 NULL입니다.

이 worker는 LIVE·UPBIT 주문의 `LIVE_PLACED` / `LIVE_WAIT` / `LIVE_UNKNOWN` 상태만 Upbit GET으로 조회하고 DB를 동기화합니다. 새 주문 생성, 자동 재주문, 자동 취소, Telegram 승인 생성, AI 호출은 하지 않습니다. API 오류 시 미확정 상태를 유지하고 다음 cycle에서 다시 조회합니다. terminal 주문과 MOCK 주문은 polling하지 않습니다.

`LIVE_ORDER_RECONCILIATION_ENABLED=false` 또는 non-LIVE 모드에서는 조회 없이 대기합니다. Production LIVE 점검은 worker 실행과 활성화를 요구합니다. `--once`는 한 batch만 처리하고 종료하며, 실제 LIVE 환경에서는 기존 주문의 private GET이 발생합니다.

전체 로그를 따라가야 할 때:

```bash
docker compose logs -f
```

## 서버 런타임 안전 점검

제한적 라이브 테스트 전이나 운영 상태 점검 시 서버 런타임 안전 점검 스크립트를 실행합니다.

일반 점검:

```bash
bash scripts/check_server_runtime_safety.sh
```

제한적 라이브 테스트 직전 엄격 점검:

```bash
bash scripts/check_server_runtime_safety.sh --strict-live
```

현재 scheduled Production LIVE 점검:

```bash
bash scripts/check_server_runtime_safety.sh --production-live
```

이 스크립트는 읽기 전용 점검만 수행합니다. 컨테이너를 시작, 중지, 재생성, 삭제하지 않으며 주문도 실행하지 않습니다.

## 수동 AI 분석 실행

서버에서 1회 AI 분석을 수동 실행합니다.

```bash
docker compose --profile manual run --rm ai-trade-analysis
```

이 명령은 시장/계좌/캔들 데이터를 수집하고 AI 추천을 생성한 뒤 Telegram 알림 또는 승인 요청을 보냅니다.

## STATIC/DYNAMIC rollout

배포 직후에는 반드시 기본 `MARKET_UNIVERSE_MODE=STATIC`과
`LIVE_DYNAMIC_MARKET_ENABLED=false`를 유지합니다. 이 상태에서는 기존
`ALLOWED_MARKETS`가 분석 목록과 LIVE 주문 allowlist 역할을 계속합니다.

DYNAMIC 분석을 검증할 때는 먼저 주문 모드를 MOCK으로 유지하고
`MARKET_UNIVERSE_MODE=DYNAMIC`만 켠 뒤 다음 진단을 실행합니다.

```bash
docker compose --profile manual run --rm ai-trade-analysis \
  python -m scripts.check_market_universe
```

진단은 OpenAI나 주문 API를 호출하지 않습니다. 전체/제외/prefilter/Top N/holdings 추가
수와 timeframe별 data quality를 확인합니다. DYNAMIC LIVE는 기존
`LIVE_ORDER_ENABLED`, confirmation, Telegram 승인에 더해
`LIVE_DYNAMIC_MARKET_ENABLED=true`가 있어야 하며, 운영 검토 없이 이 값을 켜지
않습니다.

## PostgreSQL 공개 포트 확인

서버 런타임에서는 PostgreSQL이 외부에 공개되면 안 됩니다.

```bash
docker compose ps postgres
```

`PORTS`에 다음처럼 내부 포트만 보이는 것은 정상입니다.

```text
5432/tcp
```

서버에서 다음처럼 보이면 안 됩니다.

```text
0.0.0.0:5432->5432/tcp
```

위와 같은 외부 포트 매핑이 보이면 즉시 Compose 파일 조합을 확인합니다. 서버에서는 `docker-compose.local.yml`을 함께 사용하지 않습니다.

## Operational Alerting 운영

`operational-alert-worker`는 DB와 Telegram만 사용하는 별도 프로세스입니다. 거래,
reconciliation, portfolio/PnL transaction과 결합되지 않으며 Upbit/OpenAI client를
사용하지 않습니다. 기본 설정은 다음과 같습니다.

```env
OPERATIONAL_ALERTING_ENABLED=false
OPERATIONAL_ALERT_INTERVAL_SECONDS=60
LIVE_ORDER_STALE_ALERT_AFTER_SECONDS=600
OPERATIONAL_ALERT_MAX_RETRIES=3
OPERATIONAL_ALERT_RETRY_DELAYS_MINUTES=1,5,15
```

활성화 전 읽기 전용 진단으로 stale LIVE 주문과 pending/retryable outbox 수를 확인합니다.

```bash
python -m scripts.check_operational_alerts
```

진단은 INSERT/UPDATE/commit과 Telegram/Upbit/OpenAI 호출을 하지 않습니다. Worker는
LIVE·UPBIT의 `LIVE_PLACED`, `LIVE_WAIT`, `LIVE_UNKNOWN`만 `OrderLog.created_at` 기준으로
감지합니다. 주문당 deterministic dedup key를 사용해 경고는 한 번만 만들고, 주문이
terminal이 되면 alert의 `resolved_at`만 갱신합니다. 주문이나 추천 상태는 바꾸지 않습니다.

권장 Production rollout 순서는 deploy와 migration 완료, dry-run 결과 확인,
`OPERATIONAL_ALERTING_ENABLED=true` 설정, worker recreate, 로그 확인, runtime safety
검사입니다. 설정과 worker 활성화는 운영자가 직접 수행합니다.

```bash
docker compose up -d --no-deps --force-recreate operational-alert-worker
docker compose logs --tail=100 operational-alert-worker
bash scripts/check_server_runtime_safety.sh --production-live
```

## Upbit Order Chance rollout

Order Chance preflight는 `LIVE_ORDER_CHANCE_PREFLIGHT_ENABLED=false`가 기본입니다.
비활성화 상태에서는 기존 LIVE 실행과 account/ticker 검증이 그대로 유지됩니다. 활성화하면
기존 OrderLog/identifier recovery 이후 새 주문을 만들 때만 인증된
`GET /v1/orders/chance`를 호출하고, 실패하거나 응답이 불완전하면 fail-closed합니다.

추천 단계의 5,000원 기준은 네트워크 없는 conservative pre-screen입니다. 활성화된 실제
LIVE 실행에서는 chance의 current min/max/type/balance/fee가 canonical preflight이며,
내부 건별·일일 BUY 한도는 별도로 계속 적용됩니다. 승인된 BUY 금액이나 SELL 수량은
자동으로 줄이지 않습니다.

Production rollout은 다음 순서를 사용합니다.

1. 배포 후 `LIVE_ORDER_CHANCE_PREFLIGHT_ENABLED=false`를 확인합니다.
2. `bash scripts/check_server_runtime_safety.sh --production-live`를 실행합니다.
3. 아래 read-only 진단 결과를 검토합니다.
4. `.env`를 백업한 뒤 운영자가 flag를 `true`로 변경합니다.
5. Telegram listener 등 설정을 읽는 필요한 container를 recreate합니다.
6. runtime safety와 container 로그를 다시 확인합니다.
7. 별도 인위적 주문 없이 자연스러운 다음 Telegram 승인 주문에서 관찰합니다.

```bash
python -m scripts.check_upbit_order_chance --market KRW-BTC
```

진단은 `GET /v1/orders/chance`만 호출하며 `/v1/orders`, `/v1/orders/test`, DB write,
Telegram, OpenAI 호출을 하지 않습니다. Chance GET 실패는 주문 생성 전이므로
`LIVE_FAILED`; 실제 POST 응답 불명확만 기존 `LIVE_UNKNOWN`입니다.

## Account Activity Ledger rollout

Account Activity sync는 `GET /v1/orders/closed`, `GET /v1/deposits`,
`GET /v1/withdraws`만 사용합니다. 주문조회, 입금조회, 출금조회 권한은 서로 독립적이며
출금조회 권한은 출금하기 권한이 아닙니다. 주소, TXID, Authorization, JWT는 ledger나
출력에 저장하지 않습니다.

Production rollout 순서:

1. 배포와 migration 후 `ACCOUNT_ACTIVITY_SYNC_ENABLED=false`를 확인합니다.
2. runtime safety를 실행합니다.
3. `python -m scripts.check_upbit_account_activity_access`로 세 조회 권한을 확인합니다.
4. `python -m scripts.sync_account_activities --start-at <ISO-8601>` dry-run을 실행합니다.
5. source별 count, BOT/EXTERNAL 분류, coverage와 `OUT_OF_SCOPE`를 검토합니다.
6. DB backup 후 운영자가 같은 범위에 `--apply`를 한 번 명시합니다.
7. DB row 수, unique key와 source별 sync state를 검증합니다.
8. 지속 동기화가 필요할 때만 flag를 `true`로 변경하고 worker를 recreate합니다.
9. worker 로그와 runtime safety를 다시 확인합니다.

기본 dry-run은 remote GET과 DB SELECT만 수행하며 flush/commit/cursor update가 없습니다.
`--apply`는 source별 짧은 transaction으로 activity upsert와 coverage state를 저장합니다.
한 source가 `OUT_OF_SCOPE`여도 다른 source는 독립적으로 동기화되지만, 전체 결과는
complete로 표시되지 않습니다.

## Production scheduler 관리

Production scheduled LIVE에서는 scheduler profile을 명시해 실행합니다.

```bash
docker compose --profile scheduler up -d ai-trade-scheduler
```

배포 또는 장애 조사 중 분석 실행을 막아야 할 때만 중지합니다.

```bash
docker compose --profile scheduler stop ai-trade-scheduler
```

중지 후 상태를 확인합니다.

```bash
docker compose --profile scheduler ps -a ai-trade-scheduler
```

## 오래된 종료 상태 스케줄러 컨테이너 제거

종료된 스케줄러 컨테이너가 남아 있고 정리가 필요하면 제거합니다.

```bash
docker compose --profile scheduler rm ai-trade-scheduler
```

실행 중인 컨테이너를 제거하지 않도록 먼저 `ps -a` 상태를 확인합니다.

## DB 백업 실행

서버에서 PostgreSQL 컨테이너가 실행 중인 상태에서 백업 스크립트를 실행합니다.

```bash
bash scripts/backup_db.sh
```

백업 파일은 다음 디렉터리에 생성됩니다.

```bash
${HOME}/backups
```

기본적으로 최근 10개 백업만 유지합니다. 보관 개수를 바꾸려면 다음처럼 실행합니다.

```bash
BACKUP_KEEP_COUNT=20 bash scripts/backup_db.sh
```

## 백업 파일 확인

```bash
ls -lh ${HOME}/backups/crypto_trading_bot_*.sql
```

백업 파일 권한은 `600`이어야 합니다.

```bash
ls -l ${HOME}/backups/crypto_trading_bot_*.sql
```

## 위험한 명령 주의

다음 명령은 컨테이너와 네트워크뿐 아니라 PostgreSQL 데이터 볼륨까지 삭제합니다.

```bash
docker compose down -v
```

`postgres_data` 볼륨이 삭제되면 데이터베이스 내용이 사라집니다. 서버에서는 이 명령을 가볍게 실행하지 않습니다.

일반적인 중지는 다음 명령을 사용합니다.

```bash
docker compose down
```

## AI 거래 비율과 승인

- `trade_ratio=1`의 BUY는 현재 KRW 100%를 의미하지만 BUY 최대 한도를 유지합니다.
- 수수료를 임의로 하드코딩하지 않으므로, 잔고 전체 BUY의 정확한 최종 가능액은
  향후 Upbit order-chance/수수료 정보 연동이 필요합니다.
- `trade_ratio=1`의 SELL은 추천 산정 시점 선택 자산 수량 100%를 승인 수량으로 사용합니다.
- SELL은 BUY용 최대 금액과 일일 BUY 한도를 적용하지 않습니다.
- 승인 후 SELL 실행 직전에 ticker와 잔고를 재조회하며, 승인한 수량을
  임의로 늘리거나 줄이지 않습니다.
- 누락, NaN, 무한대, 음수, 1 초과 ratio는 승인 요청을 만들지 않도록 HOLD로
  안전 변환됩니다.

## 단순 복구 메모

- 최신 서버 `.env`와 `/etc/crypto-trading-bot/secrets`를 안전하게 보관합니다.
- DB 백업 파일을 유지합니다.
- 복구는 자동으로 처리하지 말고, 상황을 확인한 뒤 수동으로 신중하게 진행합니다.
- 복구 전에는 현재 컨테이너 상태, 백업 파일 시각, `.env`와 비밀 디렉터리를 다시 확인합니다.
## Policy Promotion Gate v1

등록된 Candidate의 고정된 v1 gate를 수동 평가한다.

```bash
python -m scripts.evaluate_policy_promotion_gate --candidate-id 1
python -m scripts.evaluate_policy_promotion_gate --candidate-id 2
```

CLI decision 입력은 `--candidate-id`뿐이며 horizon, threshold, sample 수, 비용률 또는 as-of override는 지원하지 않는다. Gate는 Registration-Bounded Historical bundle을 한 번, Forward Gross와 Turnover를 각각 한 번 평가하고, 같은 결과를 Forward Cost-adjusted 계산에 재사용한다. 시작 시 캡처한 동일 Forward snapshot ceiling이 두 Forward query에 적용된다.

`INVALID_PROMOTION_DATA`는 lineage/integrity 오류(exit 1), `INSUFFICIENT_DATA`는 표본 부족(exit 0), `NOT_ELIGIBLE`은 충분한 표본의 기준 실패(exit 0), `ELIGIBLE_FOR_REVIEW`는 Shadow 검토 최소 gate 통과(exit 0)다. 21 observations, 20 transitions, 168시간은 통계적 유의성이 아니라 다음 단계 전의 deterministic engineering guardrail이다. `ELIGIBLE_FOR_REVIEW`도 실제 promotion, Candidate lifecycle 변경, Shadow 생성, Telegram 승인, 주문 또는 LIVE 정책 변경을 수행하지 않는다. 명령은 DB SELECT-only이며 외부 API를 호출하지 않는다.
## Shadow Policy Enrollment v1

현재 Candidate 1/2는 Promotion Gate가 `INSUFFICIENT_DATA`이므로 다음 preview만 권장합니다.

```bash
python -m scripts.enroll_shadow_policy --candidate-id 1
python -m scripts.enroll_shadow_policy --candidate-id 2
```

실제 eligible Candidate에 한해 운영자가 명시적으로 적용합니다.

```bash
python -m scripts.enroll_shadow_policy \
  --candidate-id <ELIGIBLE_CANDIDATE_ID> \
  --apply
```

`--apply`를 사용해도 현재 Gate 결과가 정확히 `ELIGIBLE_FOR_REVIEW`가 아니면 `GATE_NOT_ELIGIBLE`, `database_write=false`로 종료하며 INSERT하지 않습니다. Candidate당 anchor는 하나이고 정상 기존 row는 Gate 재평가 없이 `ALREADY_ENROLLED`가 됩니다. 이 명령은 Shadow runtime/evaluation을 시작하지 않고 외부 API, Telegram, 주문 또는 LIVE policy를 변경하지 않습니다. 다음 별도 단계인 Shadow Evaluation v1만 enrollment triple cutoff 이후의 snapshot을 사용해야 합니다.

## Shadow Selection Evaluation v1

현재 Production Candidate 1/2에는 Shadow Enrollment가 없어야 하므로 다음 preview의 정상 예상 결과는 `NO_SHADOW_ENROLLMENT`, `database_write=false`입니다.

```bash
python -m scripts.evaluate_shadow_policy_selection --candidate-id 1
python -m scripts.evaluate_shadow_policy_selection --candidate-id 2
```

실제로 eligible enrollment가 생성된 Candidate는 먼저 preview하고, 결과와 invocation ceiling을 확인한 뒤 적용합니다.

```bash
python -m scripts.evaluate_shadow_policy_selection \
  --candidate-id <ENROLLED_CANDIDATE_ID>

python -m scripts.evaluate_shadow_policy_selection \
  --candidate-id <ENROLLED_CANDIDATE_ID> \
  --apply
```

`--apply`는 strict triple cutoff 이후 frozen Strategy Replay snapshot의 selection evidence만 `shadow_policy_evaluations`에 저장합니다. Broad timeline의 policy/TopN mismatch도 chronology marker로 보존합니다. 이 명령은 outcome resolver, external API, Telegram, 주문, LIVE ranking 또는 Shadow runtime을 호출·변경하지 않습니다.

### Shadow Selection Automatic Evaluation v1

자동 평가는 새 service/container 없이 기존 `recommendation-outcome-worker`의 세 번째 독립 cycle로 실행됩니다.

```text
SHADOW_SELECTION_EVALUATION_ENABLED=false
SHADOW_SELECTION_EVALUATION_INTERVAL_SECONDS=300
```

배포 기본값은 false입니다. rollout 시 Production `.env`에서 명시적으로 true로 설정하고 기존 배포 절차로 `recommendation-outcome-worker`를 recreate한 뒤 cycle summary 로그와 `check_server_runtime_safety.sh --production-live` 결과를 확인합니다. Candidate 목록은 cycle 시작 시 `shadow_policy_enrollments.id ASC`로 한 번 고정되며 각 Candidate는 별도 transaction으로 평가됩니다. 자동 Enrollment와 Promotion Gate는 실행하지 않습니다.

현재 Production Candidate 1/2처럼 Shadow Enrollment가 없다면 automation이 켜져 있어도 `enrollment_count=0`, `created_evaluation_count=0`이 정상입니다. Shadow만 독립 점검할 때는 위 수동 `evaluate_shadow_policy_selection` CLI를 사용합니다.

## Shadow Performance Evidence v1

저장된 Shadow selection과 기존 Candidate Outcome만 사용하는 read-only 진단입니다. invocation 시작 시 stable UTC as-of와 Shadow evaluation snapshot ceiling을 한 번씩 고정하며, Offline Replay, outcome 생성, price resolver, 외부 API, DB write, Shadow Review/Promotion 또는 LIVE 변경을 수행하지 않습니다.

```bash
python -m scripts.evaluate_shadow_policy_performance \
  --candidate-id <ID> \
  --horizon 60 \
  --horizon 240 \
  --horizon 1440 \
  --fee-rate 0.0005 \
  --spread-cost-rate 0.0005 \
  --slippage-rate 0.001
```

Gross는 저장된 `SUCCESS` evaluation만 비교합니다. Turnover와 비용은 broad timeline의 실제 인접 row가 모두 `SUCCESS`일 때만 계산하므로 context/baseline/replay break를 건너뛰지 않습니다. Horizon outcome이 아직 성숙하지 않은 상태와 transition 부족은 안전한 component status이며 promotion 실패를 뜻하지 않습니다. 현재 Production Candidate 1/2에 Shadow Enrollment가 없다면 정상 결과는 `NO_SHADOW_ENROLLMENT`, exit code 0, `database_write=false`입니다.

## Shadow Review Gate v1

Shadow Review Gate는 저장된 Enrollment의 pre-Shadow Gate provenance를 검증한 다음 `ShadowPolicyPerformanceService`를 정확히 한 번 호출하는 read-only 수동 점검입니다. 현재 Forward evidence, Historical evidence 또는 Offline Replay를 재평가하지 않습니다.

```bash
python -m scripts.evaluate_shadow_review_gate --candidate-id <ID>
```

CLI 입력은 `--candidate-id`만 허용합니다. v1 정책은 코드에 고정되며 required horizon은 60/240/1440분, fee/spread/slippage는 0.0005/0.0005/0.001입니다. 최소 관찰 기간 336시간, successful selection 42개, horizon별 Gross 35개, turnover transition 40개, horizon별 cost-adjustable 35개 및 coverage 0.80, continuity break 0개를 요구합니다. Gross supportive horizon은 mean delta >= 0, Cost supportive horizon은 mean delta > 0, median delta >= 0, win rate >= 0.55이며 각각 3개 중 2개 이상이어야 합니다. 모든 horizon에 -0.50 percentage-point catastrophic floor를 적용합니다.

종료 코드는 `INVALID_REVIEW_DATA`만 1이고, `NO_SHADOW_ENROLLMENT`, `INSUFFICIENT_DATA`, `NOT_ELIGIBLE`, `ELIGIBLE_FOR_PROMOTION_REVIEW`는 0입니다. 현재 Production Candidate 1/2에 enrollment가 없다면 `NO_SHADOW_ENROLLMENT`가 정상입니다. Eligible 결과도 Promotion을 수행하지 않으며 DB write, 외부 호출, Telegram, 주문, Shadow runtime 또는 LIVE policy 변경이 없습니다.

## Human-approved Promotion v1

먼저 Preview에서 `current_review_decision_signature`와 모든 Review evidence를 사람이 확인합니다.

```bash
python -m scripts.approve_shadow_policy_promotion --candidate-id <ID>
```

승인하려면 Preview에서 확인한 signature를 그대로 제출합니다.

```bash
python -m scripts.approve_shadow_policy_promotion \
  --candidate-id <ID> \
  --expected-review-decision-signature "<EXACT_SIGNATURE_FROM_PREVIEW>" \
  --apply
```

Apply는 Review Gate를 다시 평가합니다. Shadow evidence가 추가되거나 decision이 달라져 signature가 불일치하면 `REVIEW_DECISION_CHANGED`가 정상이며 INSERT하지 않습니다. 새 Preview부터 다시 확인해야 하며 `--force`, horizon/cost/as-of/ceiling override는 없습니다. Candidate와 Enrollment당 하나의 immutable approval만 허용하고 동일 승인 재실행은 `ALREADY_APPROVED`입니다.

현재 Production Candidate 1/2는 `NO_SHADOW_ENROLLMENT`이므로 승인 대상이 아닙니다. Approval row 생성은 LIVE policy activation, ranking runtime 변경, 주문 변경 또는 Limited LIVE Canary 시작이 아닙니다.

## Limited LIVE Canary v1-B

상태 확인은 DB/settings만 읽고 run slot을 예약하거나 외부 API를 호출하지 않습니다.

```bash
python -m scripts.check_live_policy_canary
```

현재 Human-approved Promotion을 Activation 후보로 검증하는 Preview도 read-only입니다.

```bash
python -m scripts.start_live_policy_canary \
  --promotion-approval-id <ID>
```

기술적으로 Apply는 Preview에서 확인한 exact Approval signature를 요구합니다.

```bash
python -m scripts.start_live_policy_canary \
  --promotion-approval-id <ID> \
  --expected-approval-signature "<EXACT_SIGNATURE>" \
  --apply
```

Apply 성공 시 기존 v1-A Activation과 v1-B immutable Safety Binding이 한 transaction에서
생성됩니다. 고정 BUY cap은 10,000원/건, 30,000원/일이며 CLI/env override가 없습니다.
SELL은 이 cap에서 제외됩니다. 주문 분류는 현재 Canary 상태가 아니라 recommendation 생성
당시 persisted lineage를 사용합니다.

중지 preview와 apply:

```bash
python -m scripts.stop_live_policy_canary --canary-activation-id <ID>
python -m scripts.stop_live_policy_canary \
  --canary-activation-id <ID> \
  --expected-activation-signature "<EXACT_SIGNATURE>" \
  --apply
```

Stop은 immutable termination event만 만들며 기존 주문을 취소하지 않습니다. 다음
MarketUniverse부터 `BASELINE_STOPPED`를 사용하고, 이미 생성된 Canary recommendation에는
계속 Canary BUY cap을 적용합니다.

배포 직후 정상 smoke 결과는 `mode=BASELINE_NO_CANARY`, `active_canary_count=0`,
`database_write=false`, `external_calls=false`입니다. 그 뒤 기존
`bash scripts/check_server_runtime_safety.sh --production-live`도 그대로 통과해야 합니다.
Canary metadata 오류는 Baseline fallback이지만 DB infrastructure 오류는 pipeline failure이며,
ACTIVE Canary는 정확히 48시간 또는 6회 MarketUniverse 시도 중 먼저 도달하는 경계까지만
사용됩니다. 실패한 분석도 이미 예약된 시도이면 횟수에 포함됩니다.

기존 worker를 `--once`로 실행하는 명령은 다음과 같습니다.

```bash
docker compose run --rm recommendation-outcome-worker \
  python -m scripts.run_recommendation_outcome_worker --once
```

주의: `--once`는 현재 활성화된 Recommendation, Research, Shadow cycle을 각각 한 번 실행합니다. Shadow-only cycle은 DB-only이고 price resolver나 Upbit/OpenAI/Telegram/CoinGecko를 호출하지 않지만, 다른 두 analytics flag가 true면 해당 public-price cycle도 함께 실행됩니다.

## LIVE Canary Evidence v1

다음 on-demand 명령은 지정한 Activation의 persisted Evidence를 읽기 전용으로 출력합니다.

```bash
python -m scripts.evaluate_live_canary_evidence \
  --canary-activation-id <ID>
```

CLI에는 `--apply`, `--as-of`, `--ceiling`, `--promote` 옵션이 없습니다. 전용
PostgreSQL `REPEATABLE READ READ ONLY` snapshot 안에서 SELECT만 수행하며 DB row,
ranking policy, Canary state 또는 주문을 변경하지 않습니다. Upbit/OpenAI/Telegram 등
외부 API도 호출하지 않습니다. 출력의 Bot PnL과 Portfolio delta는 account-wide context로만
해석하고, Recommendation Outcome은 actual execution PnL로 해석하지 않습니다.

## LIVE Canary Review Gate v1

다음 on-demand 명령은 지정한 Activation의 fresh Evidence v1을 먼저 생성하고 immutable
Review policy를 read-only로 적용합니다.

```bash
python -m scripts.evaluate_live_canary_review_gate \
  --canary-activation-id <ID>
```

CLI에는 `--apply`, threshold/min-run override, `--as-of`, `--ceiling`, `--force`,
`--promote`가 없습니다. Upbit/OpenAI/Telegram/CoinGecko 호출, DB write, policy/order/Canary
state 변경, Promotion을 수행하지 않습니다.

상태는 `NO_CANARY`, `INVALID_CANARY_DATA`, `INSUFFICIENT_DATA`, `NOT_ELIGIBLE`,
`ELIGIBLE_FOR_FULL_LIVE_REVIEW`입니다. Known safety failure가 sample insufficiency에 가려지지
않도록 `INVALID > FAIL > INSUFFICIENT > PASS` 순서로 판정합니다. 마지막 상태도 사람의 Full
LIVE 검토 후보일 뿐 자동 Promotion 또는 activation이 아닙니다.

## Human-approved Full LIVE Promotion v1

먼저 read-only Preview로 현재 fresh Review를 확인합니다.

```bash
python -m scripts.approve_full_live_policy_promotion \
  --canary-activation-id <ID>
```

Preview signature는 informational only이며 다음 Apply invocation의 입력으로 재사용하지
않습니다. 실제 승인 시 같은 process가 fresh Review를 정확히 한 번 평가하고 summary와 exact
`review_decision_signature`를 출력한 뒤 TTY에서 그 signature를 직접 입력받습니다.

```bash
python -m scripts.approve_full_live_policy_promotion \
  --canary-activation-id <ID> \
  --apply
```

non-interactive/EOF, signature mismatch, noneligible Review는 fail closed이며 DB write가
없습니다. 확인이 성공하면 read-only Review Session을 종료하고 별도 write Session이
immutable provenance를 재검증해 approval audit row 하나만 INSERT합니다. 확인 뒤
Review/Evidence 재평가는 없습니다. Approval 생성은 Full LIVE activation, ranking runtime
변경, Canary cap 제거 또는 주문 변경이 아니며 외부 API를 호출하지 않습니다.
