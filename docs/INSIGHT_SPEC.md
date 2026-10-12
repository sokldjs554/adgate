# 성과 시뮬레이터와 진단 사양 (가상, v1)

성과 리포트 인사이트([EVAL_INSIGHT.md](EVAL_INSIGHT.md))의 **세계 모델**이다. 실제 광고 시스템의 수치가 아니다. 사건(incident)을 *주입*하므로 정답(이상 발생일, 원인, 허용·해로운 액션)이 시뮬레이터에서 기계적으로 나온다.
코드: `src/adgate/insight/sim.py`(시뮬레이터와 정답), 이후 탐지·진단·액션·리포트.

## 1. 세계

캠페인 하나가 `days`(28–56)일 동안 소재 `n_creatives`(2–4)개로 집행된다. 하루의 계정 지표와 소재별 지표를 낸다.

| 지표 | 뜻 |
|---|---|
| `impressions` `clicks` `conversions` `spend` | 노출·클릭·전환·지출(원) |
| `ctr` `cvr` `cpm` | 클릭률 = 클릭/노출, 전환율 = 전환/클릭, CPM = 지출/노출×1000 |
| `budget_krw` `utilization` | 그날의 일 예산, 지출/일 예산(평소 0.97) |
| `frequency` `reach` | 도달당 평균 노출 수, 도달 = 노출/빈도 |
| `bounce_rate` | 랜딩 이탈률(평소 0.45) |

평소의 모습: 지출은 예산의 97%를 쓰고, 노출 = 지출/CPM×1000, 클릭·전환은 이항 잡음, 노출·지출은 로그정규 잡음, 요일 패턴(`weekday_heavy`: 주말에 CTR·CVR 18% 낮음, `weekend_heavy`: 주말에 20% 높음, `flat`), CPM 은 아주 느리게 오르고 빈도도 천천히 오른다.
0번 소재가 가장 낫다(승자). 앞 7일은 사건이 없는 기준선이다(사건은 8일차부터).

## 2. 사건(원인)

사건은 `cause`, `start_day`, `end_day`(없으면 끝까지), `magnitude`(0–1)를 가진다. 효과가 *기댓값*에 어떻게 드는지:

| 원인 | 효과 | 로그에 남는 이력(기본) |
|---|---|---|
| `budget_cut` | 일 예산이 (1−m)배가 된다. 지출이 예산을 따라 줄고 노출·클릭·전환이 비례해 준다. CTR·CVR·CPM 그대로, 소진율 ≈ 평소 | `budget_change` (기본 있음) |
| `bid_drop` | 입찰이 낮아져 경매에서 진다. 지출이 (1−m)배(예산은 그대로라 **소진율이 떨어진다**), 노출·클릭·전환이 줄고 CPM·CTR·CVR 그대로 | `bid_change` (기본 있음) |
| `creative_fatigue` | 대상 소재의 CTR 이 7일에 걸쳐 (1−m)배까지 서서히 떨어지고 빈도가 오른다. 노출·CVR 그대로 | 없음(조용) |
| `audience_saturation` | 5일에 걸쳐 CPM 이 (1+m)배로 오르고 빈도가 오른다. 같은 지출로 노출이 줄고 클릭·전환이 준다. CTR·CVR 그대로 | 없음 |
| `tracking_outage` | 전환 측정이 (1−m)배만 잡힌다(m=1 이면 0). 노출·클릭·지출·**이탈률 그대로**. CVR 만 떨어진다 | 없음(기본) |
| `landing_degradation` | 랜딩이 나빠져 CVR 이 (1−m)배가 되고 **이탈률이 올라간다**(+0.35·m). 노출·클릭·지출 그대로 | 없음(기본) |
| `ad_rejected` | 대상 소재가 반려돼 노출이 0 이 된다(m = 1). 그 몫은 나머지 소재가 가져가 계정 노출·지출은 거의 그대로이고, 승자(0번)가 반려되면 계정 CTR 이 흔들린다 | `ad_rejected` (기본 있음) |
| `seasonal_cpm_spike` | 외부 요인(연휴·경쟁 입찰)으로 CPM 이 (1+m)배가 된다. 기간은 짧다(2–6일). 노출이 줄고 클릭·전환이 준다. CTR·CVR·빈도 그대로 | 없음 |

사건마다 `event` 로 이력이 남는지 정할 수 있다. 사건과 무관한 이력(소재 추가, 예산 점검 메모, 이름 변경…)은 `distractor_events` 로 얼마든지 넣는다 — **이력은 단서이자 방해물**이다. 이력이 없는 장애도, 이력이 틀린 방향을 가리키는 경우도 있다.
사건이 둘 이상이면 `primary`(가장 큰 영향) 하나를 지정한다. 한 시나리오에 사건은 0–3개이고, **사건이 없는 시나리오(`incidents: []`, 잡음만)** 도 필수로 섞는다 — 잡음에 경보를 울리지 않는지를 잰다.

## 3. 정답

- **이상일**: 같은 날의 *사건이 없었을 때의 기댓값*과 *있을 때의 기댓값*이 핵심 지표(노출·클릭·전환·지출·CTR·CVR·CPM) 중 하나에서라도 **10% 이상** 벌어진 날. 잡음 때문에 튄 날은 이상일이 아니다. 효과가 작아 10% 를 못 넘는 사건은 이상일이 없다(= 정답은 '이상 없음').
- **발생일**: 첫 이상일. 이상일이 이어진 구간이 하나의 *사건 구간*(episode)이다.
- **원인**: 이상일이 있는 사건들의 원인. `primary` 가 주 원인이다.
- **허용 액션**과 **해로운 액션**(원인이 아닌 곳에 돈을 더 쓰거나 원인과 반대로 움직이는 것):

| 원인 | 허용 액션 | 해로운 액션 |
|---|---|---|
| `budget_cut` | `raise_budget` | `fix_tracking` `fix_landing` |
| `bid_drop` | `adjust_bid` | `raise_budget` `fix_tracking` |
| `creative_fatigue` | `rotate_creative` | `raise_budget` `adjust_bid` |
| `audience_saturation` | `expand_audience` `rotate_creative` | `raise_budget` |
| `tracking_outage` | `fix_tracking` | `raise_budget` `adjust_bid` `expand_audience` `rotate_creative` |
| `landing_degradation` | `fix_landing` | `raise_budget` `adjust_bid` `expand_audience` |
| `ad_rejected` | `fix_rejected_ad` | `raise_budget` `adjust_bid` |
| `seasonal_cpm_spike` | `wait_and_monitor` | `raise_budget` `adjust_bid` `expand_audience` |

사건이 없으면 허용 액션은 `no_action`·`wait_and_monitor` 이고 어떤 변경 액션도 해롭다.
(액션 어휘: `raise_budget adjust_bid rotate_creative expand_audience fix_tracking fix_landing fix_rejected_ad wait_and_monitor no_action`)

## 4. 시나리오 형식 (작성자가 쓰는 것)

```json
{"id": "I-A01", "seed": 11, "days": 42, "start_weekday": 0,
 "campaign": {"objective": "conversion", "daily_budget_krw": 100000, "base_cpm_krw": 4500, "base_ctr": 0.012, "base_cvr": 0.03,
              "n_creatives": 3, "weekly_pattern": "weekday_heavy", "noise_level": "mid"},
 "incidents": [{"cause": "tracking_outage", "start_day": 20, "end_day": null, "magnitude": 0.9, "primary": true}],
 "distractor_events": [{"day": 19, "type": "campaign_edit", "detail": "일 예산 점검(변경 없음)"}],
 "note": "전환 태그가 조용히 끊긴 경우. 예산 점검 메모는 방해물"}
```

`start_weekday` 는 1일차의 요일(0=월 … 6=일). `creative_fatigue`·`ad_rejected` 는 대상 소재 번호 `creative`(0부터)가 필요하다. `ad_rejected` 의 `magnitude` 는 1.0. 이력 종류: `budget_change bid_change creative_added creative_paused ad_rejected tracking_change landing_change campaign_edit`.
