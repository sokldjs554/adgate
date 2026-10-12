# 사람 실험 프로토콜: 시스템 제안이 검토자를 빠르고 정확하게 하는가, 틀린 제안까지 따르게 하는가

이 문서는 **사람이 오면 바로 돌릴 수 있게 준비한 것**이다. 지금까지 사람 데이터는 없다. 코드·책자·분석은 시뮬레이션 참가자와 브라우저 시험으로 검증했다([EVAL_LABELING.md](EVAL_LABELING.md)).

## 1. 질문과 결과 지표

시스템이 소재마다 보여 주는 제안(엔진 판정 + 발견된 규칙과 인용)을 검토자가 보면:

| 지표 | 정의 | 역할 |
|---|---|---|
| **속도** `log_time` | 검토자 × 조건의 소재당 걸린 시간(중앙값)의 로그, 차이 = assisted − unassisted | **주 결과 1** |
| **틀린 제안 따르기** `follow_wrong` | 엔진이 틀린 소재(층 `engine_wrong`)에서 검토자 판정이 엔진 판정과 같은 비율 | **주 결과 2** |
| 정확도 `accuracy` | 판정이 정답과 같은 비율 | 탐색적 |
| 위험 승인율 `unsafe_approve` | 정답이 APPROVE 가 아닌 소재를 APPROVE 한 비율 | 탐색적 |
| 안전 손실 `safety_cost` | 판정 오류의 가중 손실(위험 승인 3·2, 정상 반려 2, 정상 보류 1 …; D-15 와 같은 표) | 탐색적 |

**주 결과를 둘로 정한 이유**: 시뮬레이션 검출력에서 속도는 검토자 8명부터, 틀린 제안 따르기는 12명부터 80% 를 넘고, 정확도·위험 승인율은 24명에서도 80% 미만이었다(9.3절 표, 가정한 효과 크기 기준). 정확도에서 제안이 돕는 만큼(+)과 틀린 제안을 따르는 만큼(−)이 상쇄되어 순효과가 작을 것이라고 보았다.
주 결과가 둘이므로 유의수준은 0.025(Bonferroni)로 한다. 탐색적 지표는 구간만 보고하고 p 값으로 결론 내리지 않는다.

## 2. 설계

**피험자 내 교차.** 검토자 한 명이 같은 소재 48건을 모두 판정한다. 절반(24건)은 제안 없이, 절반은 제안과 함께. 소재를 두 반쪽 A·B 로 나눠(층별로 반반) 검토자 짝마다 어느 쪽에 제안을 붙일지 맞바꾸고, 블록 순서(제안 먼저 / 나중)도 네 명마다 맞바꾼다. 그래서
모든 소재는 검토자의 절반에게는 제안과 함께, 나머지 절반에게는 제안 없이 보이고, 순서·학습 효과가 조건과 섞이지 않는다(검토자 수는 4의 배수가 좋다).
**소재 48건**(독립 풀 redteam + gray 에서 정답이 분쟁이 아닌 것): 엔진이 맞고 분명한 소재 16 · 엔진이 맞고 회색지대인 소재 16 · **엔진이 틀린 소재 16**(자동화 편향을 재려고 일부러 넣는다). 정답은 작성자의 판정이다 — 같은 모델 계열의 LLM 이 쓴 소재와 정답이라는 한계가 있다.
**표본 단위는 검토자다**: 한 검토자의 응답은 독립이 아니므로 검토자별 조건 차이를 구해 검토자 사이에서 검정한다(부호 뒤집기 순열 검정 + 부트스트랩 95% 구간).

## 3. 준비와 진행

```bash
adgate study kit --out study1 --reviewers 12 --seed 1      # 책자 12개 + 정답 키
```

- `study1/booklets/R01.html …` 을 검토자 한 명에게 하나씩 준다. 브라우저로 열면 되고 **인터넷이 필요 없다**(외부 요청 0, 시험으로 확인). `study1/INTERNAL/` 은 연구 담당자만 가진다(정답이 들어 있다).
- 책자: 안내 → 연습 2건(기록되지 않음) → 본 소재 48건 → 결과 파일(`R0N.json`) 받기. 소재마다 승인 · 사람 검토 · 반려 버튼을 누르고 걸린 시간이 기록된다. 제안은 해당 소재에만 보이고 제안 없는 소재의 제안은 페이지 소스에도 없다.
- 검토자에게 정책 문서(`docs/POLICY.md`)를 미리 주고 읽을 시간을 준다. 사전 설명은 모든 검토자에게 같게 한다. 제안이 틀릴 수 있다는 말은 책자 안내에 들어 있다(안 하면 편향이 더 커지고, 하면 덜 커진다 — 어느 쪽이든 모든 검토자에게 같아야 한다).
- 소요: 48건 × 약 40초 ≈ 35분. 한 번에 끝내게 하고, 중간에 그만두면 그 검토자는 분석에서 뺀다(부분 데이터로 짝지은 차이를 구하지 않는다).

## 4. 데이터와 개인정보

수집: 검토자 번호(R01 …), 소재별 판정과 걸린 시간. **이름·계정·IP 를 수집하지 않는다.** 결과 파일은 검토자의 기기에 저장되고 담당자에게 직접 전달된다. 번호와 사람의 대응표는 담당자가 따로 보관하거나 만들지 않는다.
참여는 자발적이고 언제든 멈출 수 있다. 소속 기관의 연구·개인정보 절차가 있으면 그것을 따른다(이 저장소는 그 절차를 대신하지 않는다). 모든 소재는 합성이고 업체는 가상이다.

## 5. 분석

```bash
adgate study analyze --key study1/INTERNAL/answer_key.json --responses study1/responses/*.json --out study1/analysis.json
```

지표마다 검토자 수, 차이의 평균, 95% 부트스트랩 구간(2000회), 순열 검정 p 값(검토자 14명 이하는 정확 검정). 시간은 `time_ratio`(assisted/unassisted 중앙값 시간의 비)도 낸다.

**시뮬레이션이 보여 주는 것**(가정: 제안이 맞을 때 정답률 +0.05, 틀릴 때 따르기 0.3, 시간 ×0.8; [EVAL_LABELING.md 9.3](EVAL_LABELING.md)): 검토자 12명에서 속도 <!-- num:hist.catalog_first_contact.results.study.power.by_reviewers.12.log_time|pct1 -->100.0%<!-- /num --> · 틀린 제안 따르기 <!-- num:hist.catalog_first_contact.results.study.power.by_reviewers.12.follow_wrong|pct1 -->89.0%<!-- /num --> · 정확도 <!-- num:hist.catalog_first_contact.results.study.power.by_reviewers.12.accuracy|pct1 -->13.0%<!-- /num --> · 위험 승인율 <!-- num:hist.catalog_first_contact.results.study.power.by_reviewers.12.unsafe_approve|pct1 -->28.0%<!-- /num --> 의 유의 비율. 가정한 효과가 실제보다 크면 현실의 검출력은 이보다 낮다.
**권고 검토자 수: 16명**(틀린 제안 따르기 검출력 <!-- num:hist.catalog_first_contact.results.study.power.by_reviewers.16.follow_wrong|pct1 -->98.5%<!-- /num -->, 4의 배수라 교차가 완전하다).

## 6. 한계

- 사람이 아직 안 왔다. 위 모든 수치는 시뮬레이션이다.
- 소재·정답이 같은 모델 계열 LLM 의 것이다. 사람 전문가의 정답과 다를 수 있다.
- 엔진이 틀린 소재를 일부러 1/3 넣어서, 제안을 따르는 비율은 *실제 운영에서의 비율이 아니라 이 구성에서의 값*이다.
- 검토자가 연구용이라는 것을 알고 평소보다 신중할 수 있다(호손 효과). 시간 지표는 특히 그렇다.
- 48건은 짧다. 피로·학습은 블록 순서 맞바꿈으로 상쇄하지만 없애지는 못한다.

## 부록. 엔진의 위험 승인율을 추정하는 무작위 표집

[EVAL_LABELING.md 9.5](EVAL_LABELING.md)에서 정답 없는 신호는 엔진의 위험 승인을 찾지 못했다. 위험 승인율(엔진이 APPROVE 한 소재 중 사람이 보면 위반인 비율)은 **엔진이 APPROVE 한 소재를 업종별로 무작위 표집**해서 추정한다.
비율 `p` 를 반폭 `h` 이내로 95% 신뢰도로 추정하는 데 필요한 표본 수(`n = 1.96² · p(1−p) / h²`):

| 반폭 \ 위험 승인 비율 | 5% | 10% | 20% | 30% | 50% |
|---|---|---|---|---|---|
| ±3%p | <!-- num:res.catalog_samples.results.h03.p05|int -->203<!-- /num --> | <!-- num:res.catalog_samples.results.h03.p10|int -->385<!-- /num --> | <!-- num:res.catalog_samples.results.h03.p20|int -->683<!-- /num --> | <!-- num:res.catalog_samples.results.h03.p30|int -->897<!-- /num --> | <!-- num:res.catalog_samples.results.h03.p50|int -->1068<!-- /num --> |
| ±5%p | <!-- num:res.catalog_samples.results.h05.p05|int -->73<!-- /num --> | <!-- num:res.catalog_samples.results.h05.p10|int -->139<!-- /num --> | <!-- num:res.catalog_samples.results.h05.p20|int -->246<!-- /num --> | <!-- num:res.catalog_samples.results.h05.p30|int -->323<!-- /num --> | <!-- num:res.catalog_samples.results.h05.p50|int -->385<!-- /num --> |
| ±10%p | <!-- num:res.catalog_samples.results.h10.p05|int -->19<!-- /num --> | <!-- num:res.catalog_samples.results.h10.p10|int -->35<!-- /num --> | <!-- num:res.catalog_samples.results.h10.p20|int -->62<!-- /num --> | <!-- num:res.catalog_samples.results.h10.p30|int -->81<!-- /num --> | <!-- num:res.catalog_samples.results.h10.p50|int -->97<!-- /num --> |

이 저장소 독립 풀의 엔진 위험 승인은 정답 기준 <!-- num:hist.catalog_first_contact.results.queue.independent.engine.unsafe_approves|int -->38<!-- /num -->건(전체 <!-- num:hist.catalog_first_contact.results.queue.independent.engine.n|int -->192<!-- /num -->건 중)이다 — 운영에서 이 비율이 비슷하다면 ±5%p 로 추정하려면 APPROVE 판정을 수백 건 사람이 확인해야 한다.
라벨은 이중 라벨 + 중재(`catalog/adjudicate.py`)로 모으고, 라벨러에게는 엔진 판정을 보이지 않는다(앵커링 방지).
