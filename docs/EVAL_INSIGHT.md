# 성과 인사이트 평가 사전등록

이 문서의 **1–6절은 측정 전에 쓴 것**이고 해시를 `docs/PREREGISTRATIONS.json` 이 고정한다(`adgate prereg`). 결과와 '빗나감'은 8절 이후에 덧붙인다. 예상이 틀려도 1–6절은 고치지 않는다.
세계 모델은 [INSIGHT_SPEC.md](INSIGHT_SPEC.md), 구현은 `src/adgate/insight/`.

## 1. 무엇을 알고 싶은가

합성 시뮬레이터가 사건(원인)을 *주입*하므로 정답(이상 발생일, 원인, 허용·해로운 액션)이 기계적으로 나온다. 시스템은 일별 지표·소재별 지표·변경 이력만 보고 (탐지 → 원인 → 액션 → 리포트)를 한다.

- **Q1.** 이상을 제때 알아채는가? — 정답 이상 구간을 잡은 비율과 지연.
- **Q2.** 이상이 없을 때 조용한가? — 사건 없는 시나리오와 정답 구간 밖에서의 오경보.
- **Q3.** 비슷한 증상을 내는 서로 다른 원인(예산 삭감 vs 입찰 하락 vs 외부 CPM, 측정 장애 vs 랜딩 악화, 소재 피로 vs 반려 vs 포화)을 구별하는가?
- **Q4.** 해로운 액션(전환 측정이 끊겼는데 예산 올리기 …)을 권하지 않는가? 틀린 방향을 가리키는 변경 이력에 속지 않는가?
- **Q5.** 각 구성요소(요일 맞춘 기준 창, 이틀 연속 조건, 이력 단서)가 필요한가? 단순 대시보드 규칙·이력만 보는 방식과 어떻게 다른가?
- **Q6.** 모델이 쓴 리포트는 사실에 묶이는가? 문장마다 사실 인용이 있고 숫자·원인·액션이 코드 검증을 통과하는 비율은?
- **Q7.** 같은 데이터를 모델에게 그대로 주고 도구 없이 진단하게 하면(강한 모델 포함) 시스템과 무엇이 다른가?

## 2. 데이터

[data/insight/scenarios.jsonl](../data/insight/scenarios.jsonl) — 시나리오 60건, 독립 작성자 6명이 조각마다 10건씩 시뮬레이터 사양(INSIGHT_SPEC.md)만 보고 썼다(같은 모델 계열의 별도 컨텍스트 LLM 이지 사람이 아니다). **전체가 동결된 평가셋**이다(`MANIFEST.sha256`).
시스템의 탐지기·진단기는 **설계자가 만든 개발용 시나리오**(`src/adgate/insight/devset.py`, 192건)로만 조정했고, 이 60건의 정답과 시스템 결과는 보지 않았다. 작성자가 쓰는 동안 진단기 코드를 보지 못하게 했다.

| 주제(조각) | 구성 |
|---|---|
| A | 전환 측정 장애 vs 랜딩 악화 |
| B | 예산 삭감 vs 입찰 하락 vs 외부 CPM 급등(이력이 맞음·없음·틀림) |
| C | 소재 피로 vs 소재 반려 vs 오디언스 포화(소재 3–4개) |
| D | 요일 패턴 속의 이상(주말 경계, 주 단위가 아닌 기간) |
| E | 사건이 겹치는 경우(복합 8건) |
| F | 잡음과 경계(사건 없음 4건, 경계 크기, 소규모·대규모 캠페인, 막바지 시작) |

- 구성(시뮬레이터가 계산): 시나리오 60건 중 정답이 *이상 없음*인 것 20건(사건 없음 + 효과가 10% 를 못 넘는 미세한 사건), 복합 사건 16건, 정답 이상 구간 43개. 정답에 든 원인 8종 모두(건수: 외부 CPM 10 · 랜딩 8 · 소재 피로 8 · 전환 측정 7 · 포화 7 · 예산 6 · 입찰 6 · 반려 4).
  변경 이력: 맞는 방향 16건, **틀린 방향(정답 원인과 다른 원인을 가리키는 이력) 33건**, 이력 없는 조용한 장애 9건. 방해 이력(`distractor_events`)이 있는 시나리오 56건(2개 이상 50건).
  잡음 low 18·mid 25·high 17, 요일 패턴 weekday_heavy 25·weekend_heavy 18·flat 17, 기간 28–56일, 일 예산 3만~500만 원.
- 정답은 시뮬레이터가 계산한 반사실 정답이다(INSIGHT_SPEC.md 3절). 작성자는 세계의 설정과 사건만 정하고 정답은 정하지 않는다. 사양의 공백(이력 문구·`primary` 기준 등)은 작성자 보고에서 모아 두었다.

## 3. 팔 (`src/adgate/insight/evaluate.py`)

| 팔 | 내용 |
|---|---|
| **system** | 다중 창(3·7·14일) 탐지 + 신호 서명 가중 최소제곱 진단 + 기대효용 액션 |
| no_history | 변경 이력을 단서로 쓰지 않는다 |
| no_alignment | 같은 요일로 맞추지 않고 바로 앞의 깨끗한 창과 견준다 |
| no_persistence | 이틀 연속 조건 없이 한 번 넘으면 경보 |
| naive | 흔한 대시보드 규칙: 일 단위로 직전 7일 평균과 20% 넘게 벌어지면 경보, 증상으로 액션(지출↓→예산 올리기, CTR↓→소재 교체, 전환율↓→태그 점검, CPM↑→타깃 넓히기) |
| history_only | 변경 이력만 본다: 이력이 찍힌 날 그 이력이 가리키는 원인으로 경보 |
| **writer**(LLM) | 시스템이 만든 *사실*(F1…)만 근거로 `claude-sonnet-5-5` 가 리포트를 쓰고 코드가 검증 |
| **raw**(LLM) | 일별 지표 표 + 소재별 표 + 이력을 `claude-sonnet-5-5` 와 `claude-opus-5-5` 에게 주고 도구 없이 진단·권고 |

## 4. 지표

**탐지.** 경보가 정답 이상 구간 `[시작−1, 끝+7]` 안에서 울리면 그 구간을 *잡은 것*이다. 구간 근처(`[시작−3, 끝+7]`)와 겹치지 않는 경보는 *오경보*다.
- 재현율 = 잡은 구간 / 정답 구간(43). 지연 = 경보일 − 정답 시작일(중앙값, p90, 3일·7일 안에 잡은 비율). 시작일 추정 오차(보조).
- 오경보: 오경보 구간 수, 100 캠페인-일당 오경보, *사건이 없는(정답 이상 없음) 시나리오 중 경보가 한 번이라도 있는 것*.

**진단**(경보일+3일까지의 데이터로 — 인과적. `final` 은 보조). 잡은 구간 중:
- 단일 원인 구간에서 1순위 원인이 그 구간에서 활동한 정답 원인 중 하나인가. 복합 구간에서 1순위가 `primary` 인가, 정답 원인을 모두 찾았는가.

**액션.** 경보마다: 권고 액션이 정답의 *해로운* 집합과 겹치면 해로움(오경보나 정답 이상 없음에서는 모든 변경 액션이 해롭다). 정답 구간을 잡은 경보에서 권고 중 하나가 *허용*에 있으면 유용(이상 없음에서는 권고가 전부 `wait_and_monitor`/`no_action`).

**리포트(writer).** 시스템이 잡은 구간마다(시나리오당 최대 3) 모델이 쓴 리포트가 검증(`verify_report`: 문단마다 사실 인용, 숫자가 인용한 사실의 숫자와 일치, 언급한 원인이 진단 후보, 권고 액션이 권고 집합 안이고 금지 액션 아님, 낮은 확률을 단정하지 않음)을 통과하는 비율.
템플릿 리포트(모델 없음)는 항상 통과해야 한다.

**raw.** 시나리오마다: 이상 여부 판단의 재현율·오경보(정답 이상 없음에서 이상이라고 함), 1순위 원인이 정답 원인 중 하나인 비율, 해로운 액션을 권한 비율, 시작일 오차.

모든 비율에 분모를 쓰고 Wilson 95% 구간을 붙인다. 표본이 작다: 정답 구간 43개, 정답 이상 없음 20건.

## 5. 사전 예상

측정 전의 내 믿음이다. 틀려도 고치지 않고 8절 이후에 '빗나감'이라 쓴다. 시스템은 개발용 시나리오(192건)에서 재현율 83/84, 오경보 3/108 로 *조정*됐으므로 개발용 수치는 낙관적이고 이 예상은 그만큼 깎았다.

| # | 예상 | 확률(내 믿음) |
|---|---|---|
| P1 | system 재현율(43 구간) **0.62–0.88**(점 0.76), 지연 중앙값 **≤ 4일**, 7일 안에 잡은 비율 **≥ 0.6** | 65% |
| P2 | system: 정답 이상 없음 20건 중 경보가 있는 시나리오 **≤ 4건**(점 2), 100 캠페인-일당 오경보 **≤ 0.8** | 65% |
| P3 | system 진단(alert+3): 단일 원인 구간에서 1순위 ∈ 정답 원인 **0.70–0.92**(점 0.82); 복합 구간에서 1순위 = `primary` **0.35–0.75**(점 0.55), 정답 원인 모두 찾음 **≤ 0.45**(점 0.25) | 55% |
| P4 | system 해로운 액션을 권한 경보 **≤ 6%**(점 3%), 해로운 액션이 한 번이라도 있는 시나리오 **≤ 4건** | 65% |
| P5 | naive: 정답 이상 없음 시나리오의 **≥ 70%** 에서 경보, 해로운 경보 **≥ 25%**, 1순위 원인 정확도 **≤ 0.45** | 85% |
| P6 | history_only: 재현율 **≤ 0.45**(조용한 장애를 못 봄), 해로운 경보 **≥ 30%**(틀린 방향의 이력 33건) | 80% |
| P7 | 절제: no_alignment 오경보 구간 수가 system 의 **3배 이상**; no_persistence 오경보가 system 의 **2배 이상**; no_history 의 진단 정확도는 system 과 **±0.04 이내** | 60% |
| P8 | writer 검증 통과율 **≥ 0.75**(점 0.88), 템플릿 리포트 통과율 **= 1.0**, 다음 단계 액션이 권고 집합 안인 비율 **≥ 0.9** | 65% |
| P9 | raw(sonnet·opus): 이상 없음 시나리오에서 오경보 **≥ 25%**; 해로운 액션을 권한 시나리오 **≥ 15%**; 1순위 원인 정확도는 시스템보다 **낮다**(opus 도) | 60% |
| P10 | system 시작일 추정 오차 중앙값 **≤ 3일** | 60% |

## 6. 판단 규칙 (측정 전에 정한다)

- **R1.** system 의 해로운 액션을 권한 경보가 10% 를 넘거나, 단일 원인 구간에서 해로운 권고가 한 번이라도 나오면 *안전 결함*이다: FAILURES 에 원인을 적고, 첫 접촉 수치를 보존한 뒤 진단기·액션 정책을 고친다. 고친 뒤 수치에는 '수정 후, holdout 아님'을 단다.
- **R2.** 절제 팔 중 하나가 system 보다 (재현율이 같거나 높고 오경보 구간 수가 같거나 낮다)면 그 구성요소를 기본값에서 *빼는 후보*로 적는다(기본값을 바꾸려면 새 사전등록).
- **R3.** LLM 리포트는 기본으로 사람에게 보이지 않는다. writer 검증 통과율이 0.90 이상일 때만 '검증을 통과한 문단은 노출해도 된다'고 적는다. 어느 경우에도 검증 실패 리포트는 템플릿 리포트로 대체한다.
- **R4.** raw(opus)가 system 과 1순위 원인 정확도가 같거나 높고 해로운 액션 비율이 system 의 +2%p 이내이면, "모델만 써도 경쟁력이 있다"고 적는다. 그 경우 이 구조의 가치는 정확도가 아니라 *보장*(근거 인용, 안전한 액션 정책, 감사 가능한 숫자)이라고 쓴다.
- **R5.** 어느 팔도 한 번만 잰다. 수치가 마음에 들지 않아 탐지기·진단기·프롬프트를 바꿔 같은 측정을 다시 하지 않는다(다른 설정은 새 사전등록).
- **R6.** 실호출 비용은 `--dry-run` 추정과 상한을 사용자에게 보이고 승인을 받은 뒤에 쓴다.

## 7. 프로토콜 (순서)

1. 이 문서·데이터(`data/insight/`)를 한 커밋으로 동결한다(사전등록 해시 등록). 시스템 코드는 이 커밋 이전 상태 그대로다(탐지기·진단기는 개발용 시나리오로만 조정; 절제 스위치와 평가 코드는 동작을 바꾸지 않는다).
2. 결정론 팔 6개를 한 번 잰다 → `artifacts/history/insight_first_contact.json`.
3. `adgate llm dry-run insight_llm` 으로 비용을 낸다 → 사용자 승인 → `llm-live` 로 writer·raw 를 녹음 → 깨끗한 작업트리에서 재생·집계 → 8절 이후.

## 8. 주장하지 않는 것

실제 광고 계정에서의 정확도(잡음·요일 패턴·사건이 모두 모델 안에 있다), 사건 둘 이상이 서로 *상호작용*하는 경우, 시뮬레이터에 없는 원인(경쟁사 입찰 급증, 정책 변경, 추적 지연). 작성자·진단기·모델이 같은 모델 계열이라 낙관 편향이 남는다.
전환이 하루 몇 건뿐인 소규모 캠페인에서 부분적 측정 손실은 통계적으로 구별되지 않는다 — 개발용 시뮬레이션의 검출력 곡선(`artifacts/insight_power.json`)이 그 한계를 수치로 보여 준다.

---

> 아래는 **측정 뒤에 덧붙인 결과**다. 1–6절(사전등록)은 고치지 않았다(`adgate prereg` 가 해시를 검사한다). 예상이 틀린 곳은 '빗나감'이라 적는다.

## 9. 결과 (1): 결정론 팔 — 첫 접촉 (`artifacts/history/insight_first_contact.json`)

탐지기·진단기는 사전등록 커밋 이전 상태 그대로다(`d84e4c2`). 60건을 팔마다 한 번 쟀다.

| 팔 | 재현율(43) | 지연 중앙값 | 오경보 구간 | 이상 없음 20건 중 경보 | 1순위 원인 정확(단일) | 해로운 경보 |
|---|---|---|---|---|---|---|
| system | <!-- num:hist.insight_first_contact.results.arms.system.detection.detected|rpct1 -->81.4% (35/43)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.system.detection.delay_median|f2 -->2.00<!-- /num -->일 | <!-- num:hist.insight_first_contact.results.arms.system.false_alarms.episodes|int -->0<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.system.false_alarms.null_scenarios_with_alert|rpct1 -->0.0% (0/20)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.system.diagnosis_alert3.single_top_in_set|rpct1 -->100.0% (23/23)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.system.actions.harmful|rpct1 -->0.0% (0/43)<!-- /num --> |
| no_history | <!-- num:hist.insight_first_contact.results.arms.no_history.detection.detected|rpct1 -->81.4% (35/43)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.no_history.detection.delay_median|f2 -->2.00<!-- /num -->일 | <!-- num:hist.insight_first_contact.results.arms.no_history.false_alarms.episodes|int -->0<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.no_history.false_alarms.null_scenarios_with_alert|rpct1 -->0.0% (0/20)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.no_history.diagnosis_alert3.single_top_in_set|rpct1 -->100.0% (23/23)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.no_history.actions.harmful|rpct1 -->0.0% (0/43)<!-- /num --> |
| no_alignment | <!-- num:hist.insight_first_contact.results.arms.no_alignment.detection.detected|rpct1 -->86.1% (37/43)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.no_alignment.detection.delay_median|f2 -->2.00<!-- /num -->일 | <!-- num:hist.insight_first_contact.results.arms.no_alignment.false_alarms.episodes|int -->21<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.no_alignment.false_alarms.null_scenarios_with_alert|rpct1 -->35.0% (7/20)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.no_alignment.diagnosis_alert3.single_top_in_set|rpct1 -->92.0% (23/25)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.no_alignment.actions.harmful|rpct1 -->4.4% (3/68)<!-- /num --> |
| no_persistence | <!-- num:hist.insight_first_contact.results.arms.no_persistence.detection.detected|rpct1 -->81.4% (35/43)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.no_persistence.detection.delay_median|f2 -->2.00<!-- /num -->일 | <!-- num:hist.insight_first_contact.results.arms.no_persistence.false_alarms.episodes|int -->5<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.no_persistence.false_alarms.null_scenarios_with_alert|rpct1 -->15.0% (3/20)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.no_persistence.diagnosis_alert3.single_top_in_set|rpct1 -->95.7% (22/23)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.no_persistence.actions.harmful|rpct1 -->2.1% (1/48)<!-- /num --> |
| naive | <!-- num:hist.insight_first_contact.results.arms.naive.detection.detected|rpct1 -->81.4% (35/43)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.naive.detection.delay_median|f2 -->2.00<!-- /num -->일 | <!-- num:hist.insight_first_contact.results.arms.naive.false_alarms.episodes|int -->104<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.naive.false_alarms.null_scenarios_with_alert|rpct1 -->100.0% (20/20)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.naive.diagnosis_alert3.single_top_in_set|rpct1 -->20.8% (5/24)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.naive.actions.harmful|rpct1 -->42.7% (91/213)<!-- /num --> |
| history_only | <!-- num:hist.insight_first_contact.results.arms.history_only.detection.detected|rpct1 -->67.4% (29/43)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.history_only.detection.delay_median|f2 -->-1.00<!-- /num -->일 | <!-- num:hist.insight_first_contact.results.arms.history_only.false_alarms.episodes|int -->26<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.history_only.false_alarms.null_scenarios_with_alert|rpct1 -->90.0% (18/20)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.history_only.diagnosis_alert3.single_top_in_set|rpct1 -->27.8% (5/18)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.history_only.actions.harmful|rpct1 -->53.0% (35/66)<!-- /num --> |

### 9.1 사전 예상과 견줌

| # | 예상 | 측정 | 판정 |
|---|---|---|---|
| P1 | system 재현율 0.62–0.88, 지연 중앙값 ≤ 4일, 7일 안 ≥ 0.6 | 재현율 <!-- num:hist.insight_first_contact.results.arms.system.detection.detected|rpct1 -->81.4% (35/43)<!-- /num -->, 중앙값 <!-- num:hist.insight_first_contact.results.arms.system.detection.delay_median|f2 -->2.00<!-- /num -->일, 7일 안 <!-- num:hist.insight_first_contact.results.arms.system.detection.within7|rpct1 -->76.7% (33/43)<!-- /num --> | 충족 |
| P2 | 이상 없음 20건 중 경보 ≤ 4건, 100일당 오경보 ≤ 0.8 | <!-- num:hist.insight_first_contact.results.arms.system.false_alarms.null_scenarios_with_alert|rpct1 -->0.0% (0/20)<!-- /num -->, <!-- num:hist.insight_first_contact.results.arms.system.false_alarms.per_100_days|f2 -->0.00<!-- /num --> | 충족(오경보 0) |
| P3 | 단일 원인 1순위 0.70–0.92; 복합에서 1순위 = primary 0.35–0.75, 모두 찾음 ≤ 0.45 | 단일 <!-- num:hist.insight_first_contact.results.arms.system.diagnosis_alert3.single_top_in_set|rpct1 -->100.0% (23/23)<!-- /num -->; 복합 primary <!-- num:hist.insight_first_contact.results.arms.system.diagnosis_alert3.multi_top_is_primary|rpct1 -->66.7% (8/12)<!-- /num -->, 모두 <!-- num:hist.insight_first_contact.results.arms.system.diagnosis_alert3.multi_all_found|rpct1 -->0.0% (0/12)<!-- /num --> | 단일은 **빗나감**(예상보다 좋다), 복합 primary 는 범위 안, 모두 찾음은 범위 안이나 0 |
| P4 | 해로운 경보 ≤ 6%, 해로운 액션이 있는 시나리오 ≤ 4건 | <!-- num:hist.insight_first_contact.results.arms.system.actions.harmful|rpct1 -->0.0% (0/43)<!-- /num -->; 시나리오 <!-- num:hist.insight_first_contact.results.arms.system.actions.scenarios_with_harmful_action|rpct1 -->0.0% (0/60)<!-- /num --> | 충족(0) |
| P5 | naive: 이상 없음 중 경보 ≥ 70%, 해로운 경보 ≥ 25%, 정확도 ≤ 0.45 | <!-- num:hist.insight_first_contact.results.arms.naive.false_alarms.null_scenarios_with_alert|rpct1 -->100.0% (20/20)<!-- /num -->; <!-- num:hist.insight_first_contact.results.arms.naive.actions.harmful|rpct1 -->42.7% (91/213)<!-- /num -->; <!-- num:hist.insight_first_contact.results.arms.naive.diagnosis_alert3.all_top_in_set|rpct1 -->25.7% (9/35)<!-- /num --> | 충족 |
| P6 | history_only: 재현율 ≤ 0.45, 해로운 경보 ≥ 30% | 재현율 <!-- num:hist.insight_first_contact.results.arms.history_only.detection.detected|rpct1 -->67.4% (29/43)<!-- /num -->; 해로운 경보 <!-- num:hist.insight_first_contact.results.arms.history_only.actions.harmful|rpct1 -->53.0% (35/66)<!-- /num --> | 재현율은 **빗나감**(아래), 해로운 경보는 충족 |
| P7 | no_alignment 오경보 ≥ 3×, no_persistence ≥ 2×, no_history ±0.04 | <!-- num:hist.insight_first_contact.results.arms.no_alignment.false_alarms.episodes|int -->21<!-- /num -->건 / <!-- num:hist.insight_first_contact.results.arms.no_persistence.false_alarms.episodes|int -->5<!-- /num -->건 (system 0); no_history 는 모든 지표가 system 과 같다 | 충족(system 의 오경보가 0 이라 배수는 형식적) |
| P10 | 시작일 추정 오차 중앙값 ≤ 3일 | <!-- num:hist.insight_first_contact.results.arms.system.detection.onset_error_median|f2 -->0.00<!-- /num -->일 | 충족 |

- **P6 가 빗나간 이유**: 틀린 방향의 방해 이력(33건)이 진짜 사건이 시작되는 날 바로 앞뒤에 찍히는 일이 많아, 이력만 보는 방식이 '정답 구간 근처에서 경보를 울린 것'으로 세어졌다(잡은 구간의 정의가 `[시작−1, 끝+7]` 이다). 재현율은 부풀려졌지만 *원인은 거의 못 맞혔고*(<!-- num:hist.insight_first_contact.results.arms.history_only.diagnosis_alert3.all_top_in_set|rpct1 -->44.8% (13/29)<!-- /num -->) 그 경보의 절반 이상이 해로운 권고였다.
- **R2 발동**: `no_history` 는 재현율·오경보·진단·액션이 system 과 *완전히 같다*. 변경 이력은 이 60건에서 측정 가능한 이득이 없다. 기본값에서 뺄 후보로 적는다 — 이력은 판정을 바꾸지 못하고(틀린 이력 33건에도 속지 않았다) 리포트의 *설명 단서*로만 남는다. 기본값 변경은 새 사전등록이 필요하다.
- **복합 사건의 '모두 찾음' 0/12**: 진단기는 두 원인을 함께 맞춘 모형이 단일 원인보다 χ² 를 12 이상 줄일 때만 복합으로 본다. 이 문턱에서 12건 모두 한 원인만 냈다(데이터를 더 모은 `final` 진단에서도 <!-- num:hist.insight_first_contact.results.arms.system.diagnosis_final.multi_all_found|rpct1 -->8.3% (1/12)<!-- /num -->). 복합 사건의 *주원인*은 맞혔지만(8/12) 부원인은 못 찾는다 — 알려진 한계다.

### 9.2 놓친 8개 구간 (system, 알려진 실패 부류)

| 부류 | 구간 | 상황 |
|---|---|---|
| **랜딩 악화 4** | I-A03, I-D01, I-D09, I-F06 | 전환율 −18%~−45% + 이탈률 상승. 이탈률은 *진단용 지원 지표*일 뿐 경보 트리거가 아니라서, 전환 수가 적으면 전환율 z 검정만으로는 못 잡는다 |
| 소규모 전환 측정 장애 1 | I-F05 (일 4만 원, m=0.7) | 하루 전환 3–4건 — [검출력 곡선](#10-검출력-곡선)의 한계 안 |
| 작은 효과 3 | I-F08(포화 16%), I-F07(5일 CPM +12%, 일 500만 원), I-E09(9%+9% 겹침) | 10%를 겨우 넘는 효과가 잡음 속에 묻힘 |

- 놓친 구간의 *부류*(특히 랜딩 4건)는 구조적 결함이다: 이탈률처럼 잡음이 아주 작은 보조 지표를 트리거로 쓰지 않는다. 개선 후보는 '이탈률 트리거'(v2). **같은 60건으로 다시 재지 않는다(R5)** — v2 는 개발용 시나리오에서 먼저 확인하고 새 사전등록과 새 독립 시나리오로 잰다.
- 시스템이 잡은 구간 35개에서는 1순위 원인이 항상 정답 원인 중 하나였다(오진 0). 이상 없음 20건(사건 없음 15 + 미세한 사건 5)에서 경보는 한 번도 울리지 않았다.

### 9.3 층화(system)

잡음별 재현율: low <!-- num:hist.insight_first_contact.results.arms.system.by.noise.low.detected|rpct1 -->83.3% (10/12)<!-- /num --> · mid <!-- num:hist.insight_first_contact.results.arms.system.by.noise.mid.detected|rpct1 -->82.6% (19/23)<!-- /num --> · high <!-- num:hist.insight_first_contact.results.arms.system.by.noise.high.detected|rpct1 -->75.0% (6/8)<!-- /num -->.
볼륨별: 소규모(일 ≤10만) <!-- num:hist.insight_first_contact.results.arms.system.by.volume.small(<=100k).detected|rpct1 -->66.7% (4/6)<!-- /num --> · 중간 <!-- num:hist.insight_first_contact.results.arms.system.by.volume.mid.detected|rpct1 -->90.6% (29/32)<!-- /num --> · 대규모(≥100만) <!-- num:hist.insight_first_contact.results.arms.system.by.volume.large(>=1M).detected|rpct1 -->X<!-- /num -->.
원인별 잡은 구간: 랜딩 <!-- num:hist.insight_first_contact.results.arms.system.by_cause.landing_degradation.detected|rpct1 -->50.0% (4/8)<!-- /num --> · 외부 CPM <!-- num:hist.insight_first_contact.results.arms.system.by_cause.seasonal_cpm_spike.detected|rpct1 -->80.0% (8/10)<!-- /num --> · 전환 측정 <!-- num:hist.insight_first_contact.results.arms.system.by_cause.tracking_outage.detected|rpct1 -->85.7% (6/7)<!-- /num --> · 반려 <!-- num:hist.insight_first_contact.results.arms.system.by_cause.ad_rejected.detected|rpct1 -->100.0% (4/4)<!-- /num -->.

## 10. 검출력 곡선

전환 측정 장애·랜딩 악화는 *전환율*로만 보이므로 하루 전환 수가 적으면 구별하기 어렵다. 개발용 시뮬레이션(동결 셋과 무관, 시드 9개, 잡음 low·mid·high 순환, 사건은 20일차에 시작)에서 전환 측정 장애를 알아챈 비율(사건 시작 후 14일 안, 괄호는 경보 지연 중앙값)이다.
랜딩 악화도 거의 같다(`artifacts/insight_power.json`).

| 일 예산 | 하루 전환 수 | 30% 손실 | 50% 손실 | 90% 손실 |
|---|---|---|---|---|
| 10만 원 | ≈ 8건 | <!-- num:res.insight_power.results.tracking_outage.b100000_m30.detected|rpct1 -->11.1% (1/9)<!-- /num --> (지연 <!-- num:res.insight_power.results.tracking_outage.b100000_m30.delay_median|f2 -->9.00<!-- /num -->일) | <!-- num:res.insight_power.results.tracking_outage.b100000_m50.detected|rpct1 -->22.2% (2/9)<!-- /num --> (지연 <!-- num:res.insight_power.results.tracking_outage.b100000_m50.delay_median|f2 -->9.00<!-- /num -->일) | <!-- num:res.insight_power.results.tracking_outage.b100000_m90.detected|rpct1 -->100.0% (9/9)<!-- /num --> (지연 <!-- num:res.insight_power.results.tracking_outage.b100000_m90.delay_median|f2 -->5.00<!-- /num -->일) |
| 50만 원 | ≈ 39건 | <!-- num:res.insight_power.results.tracking_outage.b500000_m30.detected|rpct1 -->22.2% (2/9)<!-- /num --> (지연 <!-- num:res.insight_power.results.tracking_outage.b500000_m30.delay_median|f2 -->6.50<!-- /num -->일) | <!-- num:res.insight_power.results.tracking_outage.b500000_m50.detected|rpct1 -->100.0% (9/9)<!-- /num --> (지연 <!-- num:res.insight_power.results.tracking_outage.b500000_m50.delay_median|f2 -->3.00<!-- /num -->일) | <!-- num:res.insight_power.results.tracking_outage.b500000_m90.detected|rpct1 -->100.0% (9/9)<!-- /num --> (지연 <!-- num:res.insight_power.results.tracking_outage.b500000_m90.delay_median|f2 -->1.00<!-- /num -->일) |
| 200만 원 | ≈ 155건 | <!-- num:res.insight_power.results.tracking_outage.b2000000_m30.detected|rpct1 -->100.0% (9/9)<!-- /num --> (지연 <!-- num:res.insight_power.results.tracking_outage.b2000000_m30.delay_median|f2 -->2.00<!-- /num -->일) | <!-- num:res.insight_power.results.tracking_outage.b2000000_m50.detected|rpct1 -->100.0% (9/9)<!-- /num --> (지연 <!-- num:res.insight_power.results.tracking_outage.b2000000_m50.delay_median|f2 -->1.00<!-- /num -->일) | <!-- num:res.insight_power.results.tracking_outage.b2000000_m90.detected|rpct1 -->100.0% (9/9)<!-- /num --> (지연 <!-- num:res.insight_power.results.tracking_outage.b2000000_m90.delay_median|f2 -->0.00<!-- /num -->일) |

- 하루 전환이 8건 안팎인 캠페인에서는 전환의 절반이 새도 3주 안에는 알아채지 못한다 — 통계적으로 구별되지 않는다(전환 수가 늘수록 같은 크기의 손실을 더 빨리 알아챈다). 이것은 탐지기의 '버그'가 아니라 표본 크기의 한계이며, 알아채지 못하는 것이 *오경보보다 낫다*고 판단했다.
- 알아챘을 때는 원인(측정 장애 vs 랜딩 악화)을 거의 항상 맞혔다. 이탈률(측정 장애에서는 그대로, 랜딩 악화에서는 오름)이 가르는 신호다.


## 11. 결과 (3): 모델 팔 — 실호출 첫 접촉 (`artifacts/history/insight_llm_sonnet_first_contact*.json`)

사용자가 승인한 1단계 실호출(상한 $18 중 이 평가의 모델별 상한 $2.30 · $4.00)로 `claude-sonnet-5-5` 를 **한 번** 녹음했다(`llm-live`; 리포트 작성 <!-- num:res.llm_spend.suites.insight_llm.sonnet.calls|int -->103<!-- /num -->회 중 43 + 모델만 쓰는 기준 60). 비용 <!-- num:res.llm_spend.suites.insight_llm.sonnet.usd|usd2 -->$1.45<!-- /num -->, 실패 호출 0.
`claude-opus-5-5` 는 **부분 녹음**(60건 중 <!-- num:res.llm_spend.suites.insight_llm.opus.cases|int -->21<!-- /num -->건)에서 API 크레딧이 소진돼 멈췄다([D-17](DECISIONS.md)). 앞쪽 시나리오만 담긴 비무작위 표본이라 **측정으로 쓰지 않는다** — 아래 opus 관련 예상(P9 의 opus 부분, R4)은 *측정되지 않음*이다.

### 11.1 리포트 작성 (writer, 시스템이 만든 사실만 근거로 쓰고 코드가 검증)

| 지표 | 측정 | 사전 예상 |
|---|---|---|
| 문단마다 사실 인용 · 숫자 · 원인 · 액션 검증 통과(`verify_report`) | <!-- num:hist.insight_llm_sonnet_first_contact.results.writer.verified|rpct1 -->100.0% (43/43)<!-- /num --> | P8 ≥ 0.75 (점 0.88) |
| 다음 단계 액션이 권고 집합(+안전 액션) 안 | <!-- num:hist.insight_llm_sonnet_first_contact.results.writer.steps_subset_of_recs|rpct1 -->100.0% (43/43)<!-- /num --> | P8 ≥ 0.9 |
| 템플릿 리포트(모델 없음) 통과 | <!-- num:hist.insight_llm_sonnet_first_contact.results.writer.template_verified|rpct1 -->100.0% (43/43)<!-- /num --> | P8 = 1.0 |

- **P8 충족.** R3(0.90 이상이면 ‘검증을 통과한 문단은 노출해도 된다’): 충족 — 단, **‘검증 통과’는 ‘좋은 리포트’가 아니다.** `verify_report` 는 인용 · 숫자 · 원인 후보 · 액션 집합을 보지 읽기 쉬움 · 유용함 · 광고주가 이해하는 말인지를 보지 않는다. 사람 평가는 하지 않았다(LIMITATIONS).
- 통과율 100% 는 *모델이 규칙을 잘 따랐다*와 *검증이 느슨하다*를 가르지 못한다. 검증이 실제로 거절하는지는 날조 리포트 · 인용에 없는 숫자를 넣은 리포트로 시험한다(`tests/test_insight.py`; 모델 출력으로는 거절 사례가 나오지 않았다). 사전등록 R5 때문에 프롬프트를 일부러 나쁘게 해서 다시 재지 않았다.

### 11.2 모델만 쓴 비교 기준(raw, sonnet) vs 시스템 — 같은 60건

| 지표 | raw (sonnet) | 시스템 | 단위 |
|---|---|---|---|
| 이상이 있는 시나리오를 이상이라 함 | <!-- num:hist.insight_llm_sonnet_first_contact.results.raw.anomaly_recall|rpct1 -->80.0% (32/40)<!-- /num --> | 놓친 시나리오 <!-- num:hist.insight_llm_sonnet_first_contact_analysis.analysis.system_missed_scenarios|len -->8<!-- /num -->개(= 같은 40 중 32) | 시나리오 |
| 이상 없는 시나리오에서 오경보 | <!-- num:hist.insight_llm_sonnet_first_contact.results.raw.null_false_alarm|rpct1 -->0.0% (0/20)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.system.false_alarms.null_scenarios_with_alert|rpct1 -->0.0% (0/20)<!-- /num --> | 시나리오 |
| 1순위 원인이 정답 원인 중 하나 — 채점기 정의(첫 구간의 원인만 정답) | <!-- num:hist.insight_llm_sonnet_first_contact.results.raw.top_in_set|rpct1 -->87.5% (28/32)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.system.diagnosis_alert3.all_top_in_set|rpct1 -->100.0% (35/35)<!-- /num --> | 잡은 것 중 |
| 같은 지표, 시나리오의 모든 구간의 원인을 정답으로 침(분석) | <!-- num:hist.insight_llm_sonnet_first_contact_analysis.analysis.raw_top_in_any_episode|rpct1 -->93.8% (30/32)<!-- /num --> | — | 잡은 것 중 |
| **해로운 액션을 권한 시나리오** | **<!-- num:hist.insight_llm_sonnet_first_contact.results.raw.harmful_actions|rpct1 -->5.0% (3/60)<!-- /num -->** | **<!-- num:hist.insight_first_contact.results.arms.system.actions.scenarios_with_harmful_action|rpct1 -->0.0% (0/60)<!-- /num -->** | 시나리오 |
| 권고 중 하나가 허용 집합 | <!-- num:hist.insight_llm_sonnet_first_contact.results.raw.useful_actions|rpct1 -->83.3% (50/60)<!-- /num --> | <!-- num:hist.insight_first_contact.results.arms.system.actions.useful_on_true_alerts|rpct1 -->97.7% (42/43)<!-- /num --> | 시나리오 · 잡은 경보 |
| 시작일 추정 오차 중앙값 | <!-- num:hist.insight_llm_sonnet_first_contact.results.raw.start_error_median|f2 -->0.00<!-- /num -->일 | <!-- num:hist.insight_first_contact.results.arms.system.detection.onset_error_median|f2 -->0.00<!-- /num -->일 | 잡은 것 중 |

- **P9 는 대부분 빗나갔다.** raw 가 이상 없음에서 오경보 ≥ 25% 일 것이라는 예상은 <!-- num:hist.insight_llm_sonnet_first_contact.results.raw.null_false_alarm|rpct1 -->0.0% (0/20)<!-- /num --> 로, 해로운 액션 ≥ 15% 는 <!-- num:hist.insight_llm_sonnet_first_contact.results.raw.harmful_actions|rpct1 -->5.0% (3/60)<!-- /num --> 로 빗나갔다. 1순위 원인 정확도가 시스템보다 낮다는 부분만 맞았다. 모델만 쓴 sonnet 이 생각보다 *조용하고 정확하다* — 내가 비교 기준을 얕게 봤다.
- **R4 는 적용하지 못했다**(opus 가 필요). sonnet 에 R4 를 그대로 대 보면 1순위 정확도가 시스템보다 낮아 조건(같거나 높고)을 충족하지 못한다. “모델만 써도 경쟁력이 있다”고 쓸 근거는 *탐지·원인*에서는 부분적으로 있다(위 표) — 그러나 **해로운 액션** 열에서 갈린다.

### 11.3 틀린 곳이 서로 다르다 (`artifacts/history/insight_llm_sonnet_first_contact_analysis.json`)

- **놓친 이상: 거의 겹치지 않는다.** raw 가 놓친 시나리오 <!-- num:hist.insight_llm_sonnet_first_contact_analysis.analysis.raw_missed|len -->8<!-- /num -->개 중 시스템도 놓친 것은 <!-- num:hist.insight_llm_sonnet_first_contact_analysis.analysis.both_missed|len -->1<!-- /num -->개(I-F05, 일 예산 4만 원의 소규모 전환 측정 장애 — 표본 크기의 한계). 시스템이 놓친 <!-- num:hist.insight_llm_sonnet_first_contact_analysis.analysis.system_missed_scenarios|len -->8<!-- /num -->개 중 raw 가 잡은 것이 <!-- num:hist.insight_llm_sonnet_first_contact_analysis.analysis.system_only_missed|len -->7<!-- /num -->개(랜딩 악화 4개 · 작은 효과 3개)이고, raw 만 놓친 것이 <!-- num:hist.insight_llm_sonnet_first_contact_analysis.analysis.raw_only_missed|len -->7<!-- /num -->개다(입찰 하락 · 외부 CPM 3 · 소재 반려 2 · 예산 삭감). 리포트 4건을 읽어 보니 모델은 이것들을 ‘이미 회복했다’(I-B03) · ‘의도된 변경이다’(I-D03, 일 예산 80만 → 56만 원) · ‘다른 소재가 노출을 나눠 가져 총량이 유지됐다’(I-C02) · ‘일시적 변동이다’(I-B05)로 읽었다 — 정답(시뮬레이터가 주입한 사건은 모두 이상)과 *이상의 정의*가 다르다. 이 정의 차이는 모델의 실수인지 정답의 과함인지 사람 운영자의 판단이 필요하다(LIMITATIONS).
- **잘못 진단한 <!-- num:hist.insight_llm_sonnet_first_contact_analysis.analysis.raw_wrong_cause|len -->4<!-- /num -->개 중 둘은 채점기 정의의 산물이다**: 채점기는 첫 구간의 원인만 정답으로 보는데, 구간이 여럿인 I-A06 · I-E09 에서 모델은 *시나리오의 주원인*(다른 구간의 원인)을 말했다 (<!-- num:hist.insight_llm_sonnet_first_contact_analysis.analysis.raw_wrong_cause_but_scenario_primary|len -->2<!-- /num -->개). 이 둘을 맞는 것으로 치면 <!-- num:hist.insight_llm_sonnet_first_contact_analysis.analysis.raw_top_in_any_episode|rpct1 -->93.8% (30/32)<!-- /num --> 이고 시스템(100%)과의 차이는 줄어든다 — 위 표에 두 정의를 모두 적은 이유다. 진짜 오진은 예산 삭감 ↔ 입찰 하락을 서로 바꿔 읽은 쌍(I-B06 · I-F10)이다. 이력이 맞는 방향이 아닌 시나리오에서 모델이 이력을 따라갔다고 짐작하나 확인하지 않았다.
- **해로운 액션 <!-- num:hist.insight_llm_sonnet_first_contact_analysis.analysis.raw_harmful|len -->3<!-- /num -->건의 기제**: 둘(I-A05 · I-E02)은 *여러 액션을 나열*하면서 금지 집합의 액션(`expand_audience`)을 끼워 넣었다 — 틀린 진단이 아니라 헤징이다. 하나(I-F10)는 틀린 원인(예산 삭감)에서 나온 `raise_budget` 이다. 시스템의 액션은 기대효용 정책이 정한 집합 안에서만 나오고 금지 집합은 코드가 막으므로 같은 헤징이 구조적으로 나오지 않는다(시스템 해로운 액션 시나리오 0/60).

**사후 관찰(탐색적, 측정이 아니다)**: 놓친 이상이 거의 겹치지 않으므로 ‘시스템 경보 ∪ 모델 경보’는 이 60건에서 40개 중 39개를 잡는다. 이것은 R5 때문에 *같은 60건으로 채택하거나 다시 재지 않는다* — 모델의 제안을 ‘사람이 보라’는 REVIEW 급 경보로만 쓰는 혼성 탐지(D-14 와 같은 구조)를 새 사전등록 · 새 독립 시나리오로 재는 것이 다음 실험이다(대기열, 비용은 모델만 쓰는 기준 한 번 분량).

### 11.4 사전 예상과 견줌

| # | 예상 | 측정 | 판정 |
|---|---|---|---|
| P8 | writer 검증 통과 ≥ 0.75, 템플릿 = 1.0, 액션 ⊂ 권고 ≥ 0.9 | 100% · 100% · 100% | 충족(점 예상 0.88 보다 높다) |
| P9 | raw: 이상 없음 오경보 ≥ 25%, 해로운 액션 ≥ 15%, 1순위 정확도 < 시스템 | 0% · 5% · 87.5% < 100% (sonnet; **opus 미측정**) | **빗나감**(앞의 둘), 셋째는 충족 |
| R4 | raw(opus) 가 시스템과 비슷하면 가치는 ‘보장’이라 쓴다 | opus 미측정 | **적용 못 함** |

**남은 일**: ① opus 를 끝까지 녹음한다(크레딧 충전 · 약 $2.6 — 이미 승인된 1단계 안, 승인 대기가 아니라 *잔액 대기*). ② 혼성 탐지를 새 사전등록으로 잰다. ③ 리포트의 읽기 쉬움 · 유용함은 사람(또는 쌍대 비교) 평가가 필요하다.
