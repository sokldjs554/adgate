# 운영 리허설 사전등록 (격리 · 부하 · 섀도 · 드리프트 · SLO)

이 문서의 **1–6절은 측정 전에 쓴 것**이고 해시를 `docs/PREREGISTRATIONS.json` 이 고정한다(`adgate prereg`). 결과와 '빗나감'은 8절 이후에 덧붙인다. 예상이 틀려도 1–6절은 고치지 않는다.
구현은 `src/adgate/ops/`, 묶음은 `adgate llm offline ops`(결정론, 시드 고정)와 `adgate llm offline ops_load`(벽시계, 이 기계의 수치). 모델을 부르지 않으므로 비용이 없다.

## 1. 무엇을 알고 싶은가

실제 트래픽이 없다. 그래서 이것은 **리허설**이다: 운영 장치가 *있고*, 장애나 분포 이동을 주입하면 *알아채고*, 알아채는 데 걸리는 시간을 숫자로 아는가.

- **Q1 격리.** (a) 광고주끼리 서로의 건을 볼 수 없는가 (b) 한 광고주가 쏟아붓는 동안 다른 광고주의 지연·성공률이 버티는가 (c) judge(LLM) 가 느려지거나 죽었을 때 심사 API 전체가 느려지지 않고 결정론 경로가 그대로 fail-closed 로 답하는가.
- **Q2 부하.** 동시 접속 1 · 8 · 32 · 128 에서 지연 분포·처리량·오류는? 정한 SLO(p95 ≤ 250ms · p99 ≤ 1000ms · 가용성 ≥ 99.9%)를 어느 동시 접속까지 지키는가?
- **Q3 섀도.** 후보 엔진을 운영 엔진과 나란히 돌려 정답 없는 비교 + 사람 스팟체크로 승격 여부를 정하는 게이트가 (a) 같은 후보를 통과시키고 (b) 열화된 후보를 막고 (c) 개선된 후보를 통과시키는가?
- **Q4 드리프트.** 정답 없이 엔진 출력과 입력 메타데이터만 보는 모니터가 입력 혼합의 이동(업종 쏠림 · 위험 소재 급증 · 우회 표기 급증)을 며칠 안에 알아채는가? 사건이 없을 때 조용한가? 얼마나 작은 이동까지 못 보는가?
- **Q5 SLO·경보.** 에러 버짓의 번레이트 경보(다중 창)는 전면 장애·부분 열화·느린 소모를 제때 잡고 순간 흔들림에는 조용한가? 흔한 임계값 경보(대조군)와 무엇이 다른가?

## 2. 장치와 데이터

| 장치 | 구현 |
|---|---|
| judge 장애 격리 | `ops/guard.py` `GuardedJudge`: 호출당 시간 제한 + 동시 호출 상한(벌크헤드) + 회로 차단기(연속 실패 시 열림 → 일정 시간 뒤 시험 호출 하나). API 가 judge 를 쓰면 자동으로 감싼다(`Settings.judge_*`). 실패는 모두 `judge_unavailable` → REVIEW |
| 테넌트 격리 | 기존 API: 남의 건은 403 이 아니라 404, 목록·이벤트 범위 제한, 신원별 분당 호출 상한, 신원별 검토 대기 상한 |
| 트래픽 | `ops/traffic.py`: 평가셋 소재(dev · test · redteam · gray, 329건, **정답을 안다**)에서 요일 패턴(주말 60%)·광고주 20곳(지프 분포)으로 푸아송 스트림을 만든다. 분포 이동은 *혼합*을 바꾼다: `category_shift`(대출·투자 쏠림), `attack_wave`(정답이 APPROVE 가 아닌 소재 급증), `obfuscation_surge`(우회 표기 태그 소재 급증) |
| 드리프트 모니터 | `ops/drift.py`: 하루 신호 6개(반려율 · 검토율 · 금융 업종 비율 · 보이지 않는 문자 발견율 · 소재당 발견 수 · 제목 길이). 처음 7일이 기준 창. 신호별 z(비율은 합친 분산, 설계 효과 1.5, 연속값은 Welch), `\|z\| ≥ 4` 가 **이틀 연속**이면 경보 |
| 섀도 게이트 | `ops/shadow.py`: 판정이 달라진 비율이 10% 를 넘으면 막는다. 아니면 달라진 소재 중 40건을 사람이 맞는 쪽을 가린다(여기서는 정답 라벨, 검토자 오류율 옵션). 후보가 운영보다 더 틀리면 막는다(허용 오차 0) |
| 번레이트 경보 | `ops/slo.py`: SLO 99.9%/30일. `burn` = page (1시간·5분 창 모두 14.4×) 또는 (6시간·30분 창 모두 6×), ticket (1일·2시간 창 모두 3×) 또는 (3일·6시간 창 모두 1×). 대조군 `naive` = 5분 실패율 > 1% 면 page, 1시간 > 0.5% 면 ticket |
| 부하 | `ops/load.py`: 실제 uvicorn 서버를 스레드로 띄우고 `httpx` 클라이언트 스레드를 붙인다(루프백, 한 프로세스) |

**한계를 먼저**: 트래픽의 소재는 329건뿐이라 드리프트는 '새로운 문구'가 아니라 *혼합의 변화*만 시험한다. 기준 혼합 자체가 위험 소재 64%(정답이 APPROVE 가 아닌 것)라 `attack_wave` 는 이미 위험한 풀에서 위험 비율을 더 올리는 작은 이동이다.
부하 수치는 서버·클라이언트가 한 프로세스(GIL 공유)라 이 기계에서만 의미가 있다.

## 3. 측정

**드리프트**(시드 30개 × 35일, 이동은 14일차에 시작): 하루 200건 · 1000건 × 이동 3종 × 이동 비율 0.1 · 0.25 · 0.5. 같은 스트림을 두 모니터(system · naive)가 본다. 사건 없는 스트림(시드 30 × 두 볼륨)에서 경보가 한 번이라도 울린 스트림 수가 오경보다.
**섀도**: 풀에서 150건을 20번 뽑아 스트림으로 쓴다. 후보 — 같은 엔진(`same`), 구성요소 하나를 끈 4종(`no_normalize` · `no_exceptions` · `no_challenge` · `no_landing`), 그리고 그 4종을 운영으로 두고 기본 엔진이 후보로 들어오는 개선 4종(`fix_*`). 게이트의 결정이 *정답 기준으로 후보가 운영보다 더 틀렸는가*와 일치하는 비율을 본다.
**번레이트**: 28일 분 단위 시계열 5개 × 하루 평균 분당 요청 5 · 60 · 600. 장애 4개 — 전면 장애(실패율 50%, 20분), 부분 열화(2%, 3시간), 느린 소모(0.4% = 4×, 3일), 순간 흔들림(10%, 2분). 장애마다 첫 page·ticket 경보까지의 지연(분), 장애와 무관한 경보 구간 수.
**부하**: 동시 접속 1 · 8 · 32 · 128, 수준마다 600건 이상. 소음 이웃 — alice 가 32 스레드로 쏟아붓는 동안(분당 상한 300) carol 이 일정 속도로 100건(혼자일 때와 이웃이 쏟아붓는 동안 각각). judge 장애 — 1초 걸리는 judge 에 대해 보호 없음(시간 제한 10초) 대 보호 있음(시간 제한 0.25초, 연속 실패 3회에 회로 열림).

## 4. 지표

드리프트: 오경보 스트림 수 / 검출한 스트림 수 / 검출 지연(이동 시작일 기준, 일) 중앙값 · p90 / 이동 전에 울린 경보. 섀도: 후보별 통과·차단 수, 게이트가 옳은 비율, 달라진 소재 수. 번레이트: 장애별 경보 지연(분)과 오경보 구간 수. 부하: p50 · p95 · p99(ms), 처리량(요청/초), 5xx·연결 실패 비율, 429 수, SLO 충족 여부. 소음 이웃: carol 의 p95 가 alice 가 없을 때의 몇 배인가, carol 의 오류 수, alice 중 429 비율. judge 장애: 처음 6건과 그 뒤 p95, 중앙값.
비율에는 분모를 쓴다. 드리프트·번레이트의 표본은 시드 30개·5개로 작다.

## 5. 사전 예상

측정 전의 내 믿음이다. 틀려도 고치지 않고 '빗나감'이라 쓴다.

| # | 예상 | 확률(내 믿음) |
|---|---|---|
| O1 | 드리프트 system: 사건 없는 스트림 중 경보가 울린 것 **≤ 2/30**(두 볼륨 모두). naive 는 하루 1000건에서 **≥ 8/30** | 70% |
| O2 | `category_shift`: 이동 비율 ≥ 0.25 에서 두 볼륨 모두 system 이 **30/30 검출, 지연 중앙값 1일**(이틀 연속 조건 때문에 1일이 하한이다) | 80% |
| O3 | `category_shift` 이동 비율 0.1: 하루 1000건에서 검출 **≥ 50%**, 하루 200건에서 **≤ 30%** | 55% |
| O4 | `attack_wave`: 기준 혼합이 이미 위험 64% 라 이동이 작다 — 이동 비율 0.5 에서 하루 1000건 검출 **≥ 70%**, 이동 비율 0.1 에서는 두 볼륨 모두 **≤ 20%** | 55% |
| O5 | `obfuscation_surge`: 이동 비율 0.5, 하루 1000건에서 검출 **≥ 80%** | 60% |
| O6 | naive 는 이동 전(14일 이전)에 울린 경보가 이동 스트림의 **≥ 20%**(하루 1000건), system 은 **≤ 5%** | 55% |
| O7 | 섀도: `same` 은 20/20 통과(달라진 소재가 없다). 열화 4종은 각각 **≥ 18/20 차단** | 75% |
| O8 | 섀도: 개선 4종(`fix_*`) 중 **적어도 하나**는 판정이 10% 넘게 달라져 *개선인데도 막힌다*(≥ 5/20) — 크기 제한 규칙의 대가 | 55% |
| O9 | 섀도: 게이트 결정이 정답 기준 판단과 일치하는 비율 전체 **≥ 85%** | 60% |
| O10 | 번레이트 `burn`: 전면 장애는 모든 요청량에서 page 지연 **중앙값 ≤ 5분**, 부분 열화는 분당 60·600 건에서 **≤ 10분** | 80% |
| O11 | `burn`: 느린 소모에 page **0건**, ticket 은 **36시간 안에**(분당 60·600 건) | 80% |
| O12 | `burn`: 순간 흔들림과 장애 없는 구간에서 page 오경보 **0건**(15개 시계열 전체) | 85% |
| O13 | `naive`: 순간 흔들림에 분당 60·600 건에서 page 가 **≥ 3/5 시계열**에서 울리고, 분당 5건에서는 장애 없는 구간의 page 오경보가 5개 시계열 합계 **≥ 10** | 70% |
| O14 | 부하: 가용성 100%(5xx·연결 실패 0)를 모든 동시 접속에서 유지, p95 는 c=1 에서 **≤ 50ms**, c=8 에서 **≤ 250ms**, c=128 에서는 **250ms 초과**(대기열) | 70% |
| O15 | 소음 이웃: carol 오류 **0건**, carol p95 가 alice 없을 때의 **≤ 5배**, alice 요청의 **≥ 70%** 가 429 | 60% |
| O16 | judge 장애: 보호 있음에서 회로가 열린 뒤(7번째 요청부터) p95 가 judge 없는 경우의 **≤ 2배**, 보호 없음의 중앙값은 judge 지연의 **≥ 90%** | 85% |

## 6. 판단 규칙 (측정 전에 정한다)

- **R1.** 드리프트 system 의 사건 없는 스트림 오경보가 한 볼륨에서라도 10%(3/30 초과)를 넘으면 *모니터 결함*이다: FAILURES 에 원인을 적고 첫 접촉 수치를 보존한 뒤 임계값을 새 사전등록으로 다시 정한다. 이 측정에서 임계값을 바꿔 다시 재지 않는다.
- **R2.** 섀도 게이트가 열화 후보를 하나라도 4/20 이상 통과시키면 *게이트 결함*이다(안전 쪽 실패). 개선 후보를 막는 것(O8)은 안전 쪽 실패가 아니므로 *설계 한계*로 적고 규칙을 바꾸지 않는다.
- **R3.** 번레이트 `burn` 이 순간 흔들림이나 느린 소모에 page 를 한 번이라도 내면 원인을 적는다. 정책 문턱은 SRE 워크북의 권장값을 그대로 쓴 것이라 이 시계열에 맞춰 조정하지 않는다.
- **R4.** 부하·소음 이웃·judge 장애의 수치는 이 기계의 것이다. SLO 를 못 지키는 동시 접속이 있으면 그대로 적고 서버 설정을 바꿔 다시 재지 않는다. 용량 계획으로 읽지 않는다.
- **R5.** 어느 측정도 한 번만 한다. 수치가 마음에 들지 않아 모니터·게이트·경보 정책·서버 설정을 바꿔 다시 재지 않는다(다른 설정은 새 사전등록).

## 7. 프로토콜 (순서)

1. 이 문서와 구현·시험을 한 커밋으로 등록한다(해시 등록). 그 뒤 임계값·정책·시나리오를 바꾸지 않는다.
2. `adgate llm offline ops` → `artifacts/history/ops_first_contact.json`, `adgate llm offline ops_load` → `artifacts/history/ops_load_first_contact.json`(깨끗한 작업트리에서 한 번).
3. 8절 이후에 사전 예상과 견주고 빗나간 것을 적는다. 문서의 수치는 산출물에서 채운다(`adgate docs`).

## 8. 주장하지 않는 것

실제 운영 트래픽에서의 드리프트·장애 빈도, 실제 광고주 행동, 다중 서버·네트워크 지연·DB 경합(SQLite 단일 프로세스), 사람 검토자의 실제 오류율(섀도 스팟체크는 정답 라벨을 쓰며 검토자 오류는 옵션으로만 시험했다), 용량 계획. 격리는 *설계된 경계*(404 · 상한 · 시간 제한 · 회로)가 작동하는지만 시험했고 침투 시험이 아니다.

---

> 아래는 **측정 뒤에 덧붙인 결과**다. 1–6절(사전등록)은 고치지 않았다(`adgate prereg` 가 해시를 검사한다). 예상이 틀린 곳은 '빗나감'이라 적는다.

## 9. 결과: 첫 접촉

사전등록 커밋(`3067eac`) 그대로 한 번 쟀다(`artifacts/history/ops_first_contact.json` 결정론 · `ops_load_first_contact.json` 벽시계). 표의 수치는 보존된 첫 접촉에서 계산한 요약(`artifacts/ops_summary.json`, 새로 재지 않았다)에서 채운다.
`ops_load` 산출물의 `dirty: true` 는 코드가 아니라 같은 디렉터리의 *아직 커밋하지 않은 `ops_first_contact.json`* 때문이다(코드는 커밋 `3067eac` 와 같다).

### 9.1 드리프트 모니터

사건 없는 스트림(35일, 시드 30)에서 경보가 울린 스트림:

| 모니터 | 하루 200건 | 하루 1000건 |
|---|---|---|
| system (z≥4, 이틀 연속, 설계 효과 1.5) | <!-- num:res.ops_summary.results.drift.system.null.v200|rpct1 -->0.0% (0/30)<!-- /num --> | <!-- num:res.ops_summary.results.drift.system.null.v1000|rpct1 -->0.0% (0/30)<!-- /num --> |
| naive (하루 z≥3) | <!-- num:res.ops_summary.results.drift.naive.null.v200|rpct1 -->33.3% (10/30)<!-- /num --> | <!-- num:res.ops_summary.results.drift.naive.null.v1000|rpct1 -->46.7% (14/30)<!-- /num --> |

system 의 이동별 검출(괄호는 검출한 스트림 중 지연 중앙값, 일). 이동은 14일차에 시작한다. 이동 비율은 시작 뒤 요청 중 이동한 혼합에서 뽑는 비율이다.

| 이동 | 비율 | 하루 200건 | 하루 1000건 |
|---|---|---|---|
| 대출·투자 쏠림 | 0.1 | <!-- num:res.ops_summary.results.drift.system.cells.category_shift.v200.m10.detected|rpct1 -->0.0% (0/30)<!-- /num --> (—일) | <!-- num:res.ops_summary.results.drift.system.cells.category_shift.v1000.m10.detected|rpct1 -->100.0% (30/30)<!-- /num --> (<!-- num:res.ops_summary.results.drift.system.cells.category_shift.v1000.m10.delay_median|f2 -->2.50<!-- /num -->일) |
| 대출·투자 쏠림 | 0.25 | <!-- num:res.ops_summary.results.drift.system.cells.category_shift.v200.m25.detected|rpct1 -->100.0% (30/30)<!-- /num --> (<!-- num:res.ops_summary.results.drift.system.cells.category_shift.v200.m25.delay_median|f2 -->1.00<!-- /num -->일) | <!-- num:res.ops_summary.results.drift.system.cells.category_shift.v1000.m25.detected|rpct1 -->100.0% (30/30)<!-- /num --> (<!-- num:res.ops_summary.results.drift.system.cells.category_shift.v1000.m25.delay_median|f2 -->1.00<!-- /num -->일) |
| 대출·투자 쏠림 | 0.5 | <!-- num:res.ops_summary.results.drift.system.cells.category_shift.v200.m50.detected|rpct1 -->100.0% (30/30)<!-- /num --> (<!-- num:res.ops_summary.results.drift.system.cells.category_shift.v200.m50.delay_median|f2 -->1.00<!-- /num -->일) | <!-- num:res.ops_summary.results.drift.system.cells.category_shift.v1000.m50.detected|rpct1 -->100.0% (30/30)<!-- /num --> (<!-- num:res.ops_summary.results.drift.system.cells.category_shift.v1000.m50.delay_median|f2 -->1.00<!-- /num -->일) |
| 위험 소재 급증 | 0.1 | <!-- num:res.ops_summary.results.drift.system.cells.attack_wave.v200.m10.detected|rpct1 -->0.0% (0/30)<!-- /num --> (—일) | <!-- num:res.ops_summary.results.drift.system.cells.attack_wave.v1000.m10.detected|rpct1 -->0.0% (0/30)<!-- /num --> (—일) |
| 위험 소재 급증 | 0.25 | <!-- num:res.ops_summary.results.drift.system.cells.attack_wave.v200.m25.detected|rpct1 -->0.0% (0/30)<!-- /num --> (—일) | <!-- num:res.ops_summary.results.drift.system.cells.attack_wave.v1000.m25.detected|rpct1 -->6.7% (2/30)<!-- /num --> (<!-- num:res.ops_summary.results.drift.system.cells.attack_wave.v1000.m25.delay_median|f2 -->4.00<!-- /num -->일) |
| 위험 소재 급증 | 0.5 | <!-- num:res.ops_summary.results.drift.system.cells.attack_wave.v200.m50.detected|rpct1 -->0.0% (0/30)<!-- /num --> (—일) | <!-- num:res.ops_summary.results.drift.system.cells.attack_wave.v1000.m50.detected|rpct1 -->100.0% (30/30)<!-- /num --> (<!-- num:res.ops_summary.results.drift.system.cells.attack_wave.v1000.m50.delay_median|f2 -->1.00<!-- /num -->일) |
| 우회 표기 급증 | 0.1 | <!-- num:res.ops_summary.results.drift.system.cells.obfuscation_surge.v200.m10.detected|rpct1 -->0.0% (0/30)<!-- /num --> (—일) | <!-- num:res.ops_summary.results.drift.system.cells.obfuscation_surge.v1000.m10.detected|rpct1 -->0.0% (0/30)<!-- /num --> (—일) |
| 우회 표기 급증 | 0.25 | <!-- num:res.ops_summary.results.drift.system.cells.obfuscation_surge.v200.m25.detected|rpct1 -->0.0% (0/30)<!-- /num --> (—일) | <!-- num:res.ops_summary.results.drift.system.cells.obfuscation_surge.v1000.m25.detected|rpct1 -->96.7% (29/30)<!-- /num --> (<!-- num:res.ops_summary.results.drift.system.cells.obfuscation_surge.v1000.m25.delay_median|f2 -->2.00<!-- /num -->일) |
| 우회 표기 급증 | 0.5 | <!-- num:res.ops_summary.results.drift.system.cells.obfuscation_surge.v200.m50.detected|rpct1 -->80.0% (24/30)<!-- /num --> (<!-- num:res.ops_summary.results.drift.system.cells.obfuscation_surge.v200.m50.delay_median|f2 -->8.00<!-- /num -->일) | <!-- num:res.ops_summary.results.drift.system.cells.obfuscation_surge.v1000.m50.detected|rpct1 -->100.0% (30/30)<!-- /num --> (<!-- num:res.ops_summary.results.drift.system.cells.obfuscation_surge.v1000.m50.delay_median|f2 -->1.00<!-- /num -->일) |

naive 는 작은 이동도 더 자주 잡는다 — 대출·투자 쏠림 0.1, 하루 200건에서 <!-- num:res.ops_summary.results.drift.naive.cells.category_shift.v200.m10.detected|rpct1 -->90.0% (27/30)<!-- /num --> (system 은 <!-- num:res.ops_summary.results.drift.system.cells.category_shift.v200.m10.detected|rpct1 -->0.0% (0/30)<!-- /num -->). 대가는 오경보다: 같은 규칙이 사건 없는 스트림에서 위 표의 비율로 울리고, 이동 전에 이미 울린 스트림이 하루 1000건·대출·투자 쏠림 0.1 에서 <!-- num:res.ops_summary.results.drift.naive.cells.category_shift.v1000.m10.early|rpct1 -->10.0% (3/30)<!-- /num --> (system 은 <!-- num:res.ops_summary.results.drift.system.cells.category_shift.v1000.m10.early|rpct1 -->0.0% (0/30)<!-- /num -->)였다. 검출력과 오경보의 교환이며, naive 의 '검출' 중 일부는 이동과 무관한 우연일 수 있다(이동이 작은 위험 소재 급증·우회 표기 급증에서 검출률이 사건 없는 스트림의 오경보율에 가깝다).

### 9.2 섀도 승격 게이트

풀에서 150건씩 20번 뽑은 스트림. '정답 기준 판단'은 *후보가 운영보다 더 틀리지 않은가*(허용 오차 0)이고, 게이트가 그것과 같은 결정을 내린 비율이 '일치'다.

| 후보 | 종류 | 통과 | 차단 | 정답 기준과 일치 | 달라진 소재(150건 중 평균) |
|---|---|---|---|---|---|
| `same` | 같음 | <!-- num:res.ops_summary.results.shadow.same.allowed|rpct1 -->100.0% (20/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.same.blocked|rpct1 -->0.0% (0/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.same.right|rpct1 -->100.0% (20/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.same.disagree_mean|f2 -->0.00<!-- /num --> |
| `no_normalize` | 열화 | <!-- num:res.ops_summary.results.shadow.no_normalize.allowed|rpct1 -->0.0% (0/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.no_normalize.blocked|rpct1 -->100.0% (20/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.no_normalize.right|rpct1 -->100.0% (20/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.no_normalize.disagree_mean|f2 -->5.45<!-- /num --> |
| `no_exceptions` | 열화 | <!-- num:res.ops_summary.results.shadow.no_exceptions.allowed|rpct1 -->0.0% (0/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.no_exceptions.blocked|rpct1 -->100.0% (20/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.no_exceptions.right|rpct1 -->100.0% (20/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.no_exceptions.disagree_mean|f2 -->26.70<!-- /num --> |
| `no_challenge` | 열화 | <!-- num:res.ops_summary.results.shadow.no_challenge.allowed|rpct1 -->100.0% (20/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.no_challenge.blocked|rpct1 -->0.0% (0/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.no_challenge.right|rpct1 -->100.0% (20/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.no_challenge.disagree_mean|f2 -->2.50<!-- /num --> |
| `no_landing` | 열화 | <!-- num:res.ops_summary.results.shadow.no_landing.allowed|rpct1 -->0.0% (0/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.no_landing.blocked|rpct1 -->100.0% (20/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.no_landing.right|rpct1 -->100.0% (20/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.no_landing.disagree_mean|f2 -->13.95<!-- /num --> |
| `fix_no_normalize` | 개선 | <!-- num:res.ops_summary.results.shadow.fix_no_normalize.allowed|rpct1 -->100.0% (20/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.fix_no_normalize.blocked|rpct1 -->0.0% (0/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.fix_no_normalize.right|rpct1 -->100.0% (20/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.fix_no_normalize.disagree_mean|f2 -->5.45<!-- /num --> |
| `fix_no_exceptions` | 개선 | <!-- num:res.ops_summary.results.shadow.fix_no_exceptions.allowed|rpct1 -->0.0% (0/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.fix_no_exceptions.blocked|rpct1 -->100.0% (20/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.fix_no_exceptions.right|rpct1 -->0.0% (0/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.fix_no_exceptions.disagree_mean|f2 -->26.70<!-- /num --> |
| `fix_no_challenge` | 개선 | <!-- num:res.ops_summary.results.shadow.fix_no_challenge.allowed|rpct1 -->100.0% (20/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.fix_no_challenge.blocked|rpct1 -->0.0% (0/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.fix_no_challenge.right|rpct1 -->100.0% (20/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.fix_no_challenge.disagree_mean|f2 -->2.50<!-- /num --> |
| `fix_no_landing` | 개선 | <!-- num:res.ops_summary.results.shadow.fix_no_landing.allowed|rpct1 -->75.0% (15/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.fix_no_landing.blocked|rpct1 -->25.0% (5/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.fix_no_landing.right|rpct1 -->75.0% (15/20)<!-- /num --> | <!-- num:res.ops_summary.results.shadow.fix_no_landing.disagree_mean|f2 -->13.95<!-- /num --> |

전체 일치: <!-- num:res.ops_summary.results.shadow._all.right|rpct1 -->86.1% (155/180)<!-- /num -->.

### 9.3 번레이트 경보

28일 분 단위 시계열 5개. 셀은 *경보가 울린 시계열 수*(5개 중)와 첫 경보까지 지연 중앙값(분)이다. page 가 필요 없는 장애(순간 흔들림, 느린 소모)에서는 page 가 울리지 않는 것이 정답이다.

| 정책 | 분당 요청 | 전면 장애 page | 부분 열화 page | 느린 소모 page | 느린 소모 ticket | 순간 흔들림 page | 장애 없는 구간 page 오경보 | ticket 오경보 |
|---|---|---|---|---|---|---|---|---|
| burn | 5 | <!-- num:res.ops_summary.results.alerting.burn.rpm5.hard_outage_page.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.burn.rpm5.hard_outage_page.delay_median|f2 -->1.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.burn.rpm5.partial_degradation_page.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.burn.rpm5.partial_degradation_page.delay_median|f2 -->36.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.burn.rpm5.slow_burn_page.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.burn.rpm5.slow_burn_page.delay_median|f2 -->312.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.burn.rpm5.slow_burn_ticket.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.burn.rpm5.slow_burn_ticket.delay_median|f2 -->772.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.burn.rpm5.brief_blip_page.alerted|kn -->0/5<!-- /num --> | <!-- num:res.ops_summary.results.alerting.burn.rpm5.false_page|int -->0<!-- /num --> | <!-- num:res.ops_summary.results.alerting.burn.rpm5.false_ticket|int -->26<!-- /num --> |
| burn | 60 | <!-- num:res.ops_summary.results.alerting.burn.rpm60.hard_outage_page.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.burn.rpm60.hard_outage_page.delay_median|f2 -->1.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.burn.rpm60.partial_degradation_page.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.burn.rpm60.partial_degradation_page.delay_median|f2 -->41.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.burn.rpm60.slow_burn_page.alerted|kn -->0/5<!-- /num --> | <!-- num:res.ops_summary.results.alerting.burn.rpm60.slow_burn_ticket.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.burn.rpm60.slow_burn_ticket.delay_median|f2 -->889.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.burn.rpm60.brief_blip_page.alerted|kn -->0/5<!-- /num --> | <!-- num:res.ops_summary.results.alerting.burn.rpm60.false_page|int -->0<!-- /num --> | <!-- num:res.ops_summary.results.alerting.burn.rpm60.false_ticket|int -->2<!-- /num --> |
| burn | 600 | <!-- num:res.ops_summary.results.alerting.burn.rpm600.hard_outage_page.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.burn.rpm600.hard_outage_page.delay_median|f2 -->1.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.burn.rpm600.partial_degradation_page.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.burn.rpm600.partial_degradation_page.delay_median|f2 -->43.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.burn.rpm600.slow_burn_page.alerted|kn -->0/5<!-- /num --> | <!-- num:res.ops_summary.results.alerting.burn.rpm600.slow_burn_ticket.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.burn.rpm600.slow_burn_ticket.delay_median|f2 -->903.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.burn.rpm600.brief_blip_page.alerted|kn -->0/5<!-- /num --> | <!-- num:res.ops_summary.results.alerting.burn.rpm600.false_page|int -->0<!-- /num --> | <!-- num:res.ops_summary.results.alerting.burn.rpm600.false_ticket|int -->1<!-- /num --> |
| naive | 5 | <!-- num:res.ops_summary.results.alerting.naive.rpm5.hard_outage_page.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.naive.rpm5.hard_outage_page.delay_median|f2 -->0.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.naive.rpm5.partial_degradation_page.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.naive.rpm5.partial_degradation_page.delay_median|f2 -->9.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.naive.rpm5.slow_burn_page.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.naive.rpm5.slow_burn_page.delay_median|f2 -->45.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.naive.rpm5.slow_burn_ticket.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.naive.rpm5.slow_burn_ticket.delay_median|f2 -->45.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.naive.rpm5.brief_blip_page.alerted|kn -->4/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.naive.rpm5.brief_blip_page.delay_median|f2 -->0.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.naive.rpm5.false_page|int -->168<!-- /num --> | <!-- num:res.ops_summary.results.alerting.naive.rpm5.false_ticket|int -->44<!-- /num --> |
| naive | 60 | <!-- num:res.ops_summary.results.alerting.naive.rpm60.hard_outage_page.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.naive.rpm60.hard_outage_page.delay_median|f2 -->0.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.naive.rpm60.partial_degradation_page.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.naive.rpm60.partial_degradation_page.delay_median|f2 -->2.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.naive.rpm60.slow_burn_page.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.naive.rpm60.slow_burn_page.delay_median|f2 -->14.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.naive.rpm60.slow_burn_ticket.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.naive.rpm60.slow_burn_ticket.delay_median|f2 -->125.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.naive.rpm60.brief_blip_page.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.naive.rpm60.brief_blip_page.delay_median|f2 -->0.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.naive.rpm60.false_page|int -->13<!-- /num --> | <!-- num:res.ops_summary.results.alerting.naive.rpm60.false_ticket|int -->1<!-- /num --> |
| naive | 600 | <!-- num:res.ops_summary.results.alerting.naive.rpm600.hard_outage_page.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.naive.rpm600.hard_outage_page.delay_median|f2 -->0.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.naive.rpm600.partial_degradation_page.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.naive.rpm600.partial_degradation_page.delay_median|f2 -->2.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.naive.rpm600.slow_burn_page.alerted|kn -->3/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.naive.rpm600.slow_burn_page.delay_median|f2 -->122.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.naive.rpm600.slow_burn_ticket.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.naive.rpm600.slow_burn_ticket.delay_median|f2 -->1806.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.naive.rpm600.brief_blip_page.alerted|kn -->5/5<!-- /num --> (<!-- num:res.ops_summary.results.alerting.naive.rpm600.brief_blip_page.delay_median|f2 -->0.00<!-- /num -->분) | <!-- num:res.ops_summary.results.alerting.naive.rpm600.false_page|int -->0<!-- /num --> | <!-- num:res.ops_summary.results.alerting.naive.rpm600.false_ticket|int -->0<!-- /num --> |

### 9.4 부하 · 소음 이웃 · judge 장애 (이 기계의 수치)

기계: <!-- num:res.ops_summary.results.load.machine.cpus|int -->4<!-- /num -->코어, 서버·클라이언트가 한 프로세스(루프백). SLO 목표 — p95 ≤ 250ms · p99 ≤ 1000ms · 가용성 ≥ 99.9%.

| 동시 접속 | 요청 | 처리량(건/초) | p50 ms | p95 ms | p99 ms | 5xx | 연결 실패 | SLO p95 | SLO p99 | SLO 가용성 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | <!-- num:res.ops_summary.results.load.levels.c1.requests|int -->600<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c1.throughput_rps|f2 -->111.60<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c1.p50_ms|f2 -->8.21<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c1.p95_ms|f2 -->13.93<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c1.p99_ms|f2 -->19.55<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c1.errors_5xx|int -->0<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c1.conn_errors|int -->0<!-- /num --> | <!-- num:res.ops_summary.results.load.slo_met.c1.p95|int -->1<!-- /num --> | <!-- num:res.ops_summary.results.load.slo_met.c1.p99|int -->1<!-- /num --> | <!-- num:res.ops_summary.results.load.slo_met.c1.availability|int -->1<!-- /num --> |
| 8 | <!-- num:res.ops_summary.results.load.levels.c8.requests|int -->600<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c8.throughput_rps|f2 -->88.70<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c8.p50_ms|f2 -->63.98<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c8.p95_ms|f2 -->200.44<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c8.p99_ms|f2 -->273.44<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c8.errors_5xx|int -->0<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c8.conn_errors|int -->0<!-- /num --> | <!-- num:res.ops_summary.results.load.slo_met.c8.p95|int -->1<!-- /num --> | <!-- num:res.ops_summary.results.load.slo_met.c8.p99|int -->1<!-- /num --> | <!-- num:res.ops_summary.results.load.slo_met.c8.availability|int -->1<!-- /num --> |
| 32 | <!-- num:res.ops_summary.results.load.levels.c32.requests|int -->600<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c32.throughput_rps|f2 -->74.50<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c32.p50_ms|f2 -->266.18<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c32.p95_ms|f2 -->788.23<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c32.p99_ms|f2 -->1114.48<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c32.errors_5xx|int -->0<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c32.conn_errors|int -->0<!-- /num --> | <!-- num:res.ops_summary.results.load.slo_met.c32.p95|int -->0<!-- /num --> | <!-- num:res.ops_summary.results.load.slo_met.c32.p99|int -->0<!-- /num --> | <!-- num:res.ops_summary.results.load.slo_met.c32.availability|int -->1<!-- /num --> |
| 128 | <!-- num:res.ops_summary.results.load.levels.c128.requests|int -->1280<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c128.throughput_rps|f2 -->56.00<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c128.p50_ms|f2 -->1358.15<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c128.p95_ms|f2 -->2253.92<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c128.p99_ms|f2 -->2739.41<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c128.errors_5xx|int -->0<!-- /num --> | <!-- num:res.ops_summary.results.load.levels.c128.conn_errors|int -->0<!-- /num --> | <!-- num:res.ops_summary.results.load.slo_met.c128.p95|int -->0<!-- /num --> | <!-- num:res.ops_summary.results.load.slo_met.c128.p99|int -->0<!-- /num --> | <!-- num:res.ops_summary.results.load.slo_met.c128.availability|int -->1<!-- /num --> |

(SLO 열은 1 = 충족, 0 = 미충족.)

소음 이웃 — carol(일정 속도)의 지연: 혼자일 때 p95 <!-- num:res.ops_summary.results.load.neighbor.alone_p95_ms|f2 -->15.73<!-- /num --> ms → alice 가 쏟아붓는 동안 p50 <!-- num:res.ops_summary.results.load.neighbor.flood_p50_ms|f2 -->9.39<!-- /num --> ms · p95 <!-- num:res.ops_summary.results.load.neighbor.flood_p95_ms|f2 -->342.91<!-- /num --> ms · p99 <!-- num:res.ops_summary.results.load.neighbor.flood_p99_ms|f2 -->503.45<!-- /num --> ms (p95 가 <!-- num:res.ops_summary.results.load.neighbor.p95_inflation|f2 -->21.80<!-- /num -->배), 오류 <!-- num:res.ops_summary.results.load.neighbor.steady_errors|int -->0<!-- /num -->건. alice 요청 중 429: <!-- num:res.ops_summary.results.load.neighbor.flooder_limited|rpct1 -->80.0% (1200/1500)<!-- /num -->.

judge 장애 — judge 가 요청마다 <!-- num:res.ops_summary.results.load.judge_outage.judge_delay_s|f2 -->1.00<!-- /num -->초 걸릴 때 심사 요청 지연:

| 구성 | 요청 | 중앙값 ms | p95 ms | 7번째 요청부터 p95 ms | 처음 3건 ms |
|---|---|---|---|---|---|
| judge 없음 | <!-- num:res.ops_summary.results.load.judge_outage.no_judge.requests|int -->40<!-- /num --> | <!-- num:res.ops_summary.results.load.judge_outage.no_judge.p50_ms|f2 -->7.66<!-- /num --> | <!-- num:res.ops_summary.results.load.judge_outage.no_judge.p95_ms|f2 -->12.69<!-- /num --> | <!-- num:res.ops_summary.results.load.judge_outage.no_judge.after_first6_p95_ms|f2 -->12.69<!-- /num --> | <!-- num:res.ops_summary.results.load.judge_outage.no_judge.first3_ms.0|int -->16<!-- /num --> · <!-- num:res.ops_summary.results.load.judge_outage.no_judge.first3_ms.1|int -->11<!-- /num --> · <!-- num:res.ops_summary.results.load.judge_outage.no_judge.first3_ms.2|int -->9<!-- /num --> |
| 보호 없음(시간 제한 10초) | <!-- num:res.ops_summary.results.load.judge_outage.unguarded.requests|int -->12<!-- /num --> | <!-- num:res.ops_summary.results.load.judge_outage.unguarded.p50_ms|f2 -->1009.78<!-- /num --> | <!-- num:res.ops_summary.results.load.judge_outage.unguarded.p95_ms|f2 -->1014.72<!-- /num --> | <!-- num:res.ops_summary.results.load.judge_outage.unguarded.after_first6_p95_ms|f2 -->1011.99<!-- /num --> | <!-- num:res.ops_summary.results.load.judge_outage.unguarded.first3_ms.0|int -->1015<!-- /num --> · <!-- num:res.ops_summary.results.load.judge_outage.unguarded.first3_ms.1|int -->1013<!-- /num --> · <!-- num:res.ops_summary.results.load.judge_outage.unguarded.first3_ms.2|int -->1009<!-- /num --> |
| 보호 있음(시간 제한 0.25초, 연속 3회 실패에 회로 열림) | <!-- num:res.ops_summary.results.load.judge_outage.guarded.requests|int -->40<!-- /num --> | <!-- num:res.ops_summary.results.load.judge_outage.guarded.p50_ms|f2 -->6.53<!-- /num --> | <!-- num:res.ops_summary.results.load.judge_outage.guarded.p95_ms|f2 -->260.22<!-- /num --> | <!-- num:res.ops_summary.results.load.judge_outage.guarded.after_first6_p95_ms|f2 -->11.81<!-- /num --> | <!-- num:res.ops_summary.results.load.judge_outage.guarded.first3_ms.0|int -->266<!-- /num --> · <!-- num:res.ops_summary.results.load.judge_outage.guarded.first3_ms.1|int -->262<!-- /num --> · <!-- num:res.ops_summary.results.load.judge_outage.guarded.first3_ms.2|int -->260<!-- /num --> |

### 9.5 사전 예상과 견줌

| # | 예상 | 측정 | 판정 |
|---|---|---|---|
| O1 | system 오경보 ≤ 2/30, naive(1000건) ≥ 8/30 | system <!-- num:res.ops_summary.results.drift.system.null.v200|kn -->0/30<!-- /num --> · <!-- num:res.ops_summary.results.drift.system.null.v1000|kn -->0/30<!-- /num -->, naive(1000건) <!-- num:res.ops_summary.results.drift.naive.null.v1000|kn -->14/30<!-- /num --> | 충족 |
| O2 | 대출·투자 쏠림 ≥ 0.25: 30/30 검출, 지연 중앙값 1일 | 두 볼륨·두 비율 모두 30/30, 중앙값 1일(위 표) | 충족 |
| O3 | 쏠림 0.1: 1000건 검출 ≥ 50%, 200건 ≤ 30% | <!-- num:res.ops_summary.results.drift.system.cells.category_shift.v1000.m10.detected|rpct1 -->100.0% (30/30)<!-- /num --> · <!-- num:res.ops_summary.results.drift.system.cells.category_shift.v200.m10.detected|rpct1 -->0.0% (0/30)<!-- /num --> | 충족(1000건은 예상보다 잘 잡는다) |
| O4 | 위험 소재 급증: 0.5·1000건 ≥ 70%, 0.1 은 두 볼륨 ≤ 20% | <!-- num:res.ops_summary.results.drift.system.cells.attack_wave.v1000.m50.detected|rpct1 -->100.0% (30/30)<!-- /num -->; 0.1 은 0/30 · 0/30 | 충족(그러나 0.25·1000건은 <!-- num:res.ops_summary.results.drift.system.cells.attack_wave.v1000.m25.detected|rpct1 -->6.7% (2/30)<!-- /num --> — 문턱이 가파르다) |
| O5 | 우회 표기 급증 0.5·1000건 ≥ 80% | <!-- num:res.ops_summary.results.drift.system.cells.obfuscation_surge.v1000.m50.detected|rpct1 -->100.0% (30/30)<!-- /num --> | 충족 |
| O6 | naive 의 이동 전 경보 ≥ 20%(1000건), system ≤ 5% | naive <!-- num:res.ops_summary.results.drift.naive.cells.category_shift.v1000.m10.early|rpct1 -->10.0% (3/30)<!-- /num -->, system <!-- num:res.ops_summary.results.drift.system.cells.category_shift.v1000.m10.early|rpct1 -->0.0% (0/30)<!-- /num --> | naive 쪽 **빗나감**(10%), system 쪽 충족 |
| O7 | 섀도: `same` 20/20 통과, 열화 4종 각각 ≥ 18/20 차단 | `same` <!-- num:res.ops_summary.results.shadow.same.allowed|kn -->20/20<!-- /num --> 통과; 열화 3종은 20/20 차단, **`no_challenge` 는 <!-- num:res.ops_summary.results.shadow.no_challenge.allowed|kn -->20/20<!-- /num --> 통과** | **빗나감**(R2 발동 → F-24) |
| O8 | 개선 4종 중 적어도 하나가 개선인데도 ≥ 5/20 막힌다 | `fix_no_exceptions` <!-- num:res.ops_summary.results.shadow.fix_no_exceptions.blocked|kn -->20/20<!-- /num --> 차단, `fix_no_landing` <!-- num:res.ops_summary.results.shadow.fix_no_landing.blocked|kn -->5/20<!-- /num --> 차단 | 충족(설계 한계) |
| O9 | 게이트 결정과 정답 기준 판단의 일치 ≥ 85% | <!-- num:res.ops_summary.results.shadow._all.right|rpct1 -->86.1% (155/180)<!-- /num --> | 충족(간신히; 그러나 O7 의 구멍은 이 지표가 가린다 — 정답 기준 판단도 같은 대칭 손실을 썼기 때문이다) |
| O10 | burn: 전면 장애 page 중앙값 ≤ 5분(모든 요청량), 부분 열화 ≤ 10분(분당 60·600) | 전면 장애 <!-- num:res.ops_summary.results.alerting.burn.rpm5.hard_outage_page.delay_median|f2 -->1.00<!-- /num -->–<!-- num:res.ops_summary.results.alerting.burn.rpm600.hard_outage_page.delay_median|f2 -->1.00<!-- /num -->분; 부분 열화 <!-- num:res.ops_summary.results.alerting.burn.rpm60.partial_degradation_page.delay_median|f2 -->41.00<!-- /num -->분 · <!-- num:res.ops_summary.results.alerting.burn.rpm600.partial_degradation_page.delay_median|f2 -->43.00<!-- /num -->분 | 전면 장애 충족, 부분 열화 **빗나감** |
| O11 | burn: 느린 소모에 page 0건, ticket 36시간 안(분당 60·600) | 분당 60·600: page <!-- num:res.ops_summary.results.alerting.burn.rpm60.slow_burn_page.alerted|kn -->0/5<!-- /num --> · <!-- num:res.ops_summary.results.alerting.burn.rpm600.slow_burn_page.alerted|kn -->0/5<!-- /num -->, ticket 중앙값 <!-- num:res.ops_summary.results.alerting.burn.rpm60.slow_burn_ticket.delay_median|f2 -->889.00<!-- /num -->분 · <!-- num:res.ops_summary.results.alerting.burn.rpm600.slow_burn_ticket.delay_median|f2 -->903.00<!-- /num -->분(약 15시간). **분당 5건에서는 page <!-- num:res.ops_summary.results.alerting.burn.rpm5.slow_burn_page.alerted|kn -->5/5<!-- /num -->** | 60·600 충족, 5건 **빗나감**(R3 발동) |
| O12 | burn: 순간 흔들림·장애 없는 구간 page 오경보 0건 | 순간 흔들림 page <!-- num:res.ops_summary.results.alerting.burn.rpm60.brief_blip_page.alerted|kn -->0/5<!-- /num -->(세 요청량 모두), 장애 없는 구간 page 오경보 세 요청량 합 <!-- num:res.ops_summary.results.alerting.burn.rpm5.false_page|int -->0<!-- /num -->건 | 충족 |
| O13 | naive: 순간 흔들림에 분당 60·600 ≥ 3/5 page, 분당 5건의 page 오경보 합 ≥ 10 | 순간 흔들림 <!-- num:res.ops_summary.results.alerting.naive.rpm60.brief_blip_page.alerted|kn -->5/5<!-- /num --> · <!-- num:res.ops_summary.results.alerting.naive.rpm600.brief_blip_page.alerted|kn -->5/5<!-- /num -->; 분당 5건 page 오경보 <!-- num:res.ops_summary.results.alerting.naive.rpm5.false_page|int -->168<!-- /num -->건 | 충족 |
| O14 | 부하: 모든 동시 접속에서 가용성 100%, p95 c=1 ≤ 50ms · c=8 ≤ 250ms · c=128 > 250ms | 5xx·연결 실패 모두 0, p95 <!-- num:res.ops_summary.results.load.levels.c1.p95_ms|f2 -->13.93<!-- /num --> · <!-- num:res.ops_summary.results.load.levels.c8.p95_ms|f2 -->200.44<!-- /num --> · <!-- num:res.ops_summary.results.load.levels.c128.p95_ms|f2 -->2253.92<!-- /num --> ms | 충족 |
| O15 | 소음 이웃: carol 오류 0, p95 ≤ 5배, alice ≥ 70% 가 429 | 오류 <!-- num:res.ops_summary.results.load.neighbor.steady_errors|int -->0<!-- /num -->, p95 <!-- num:res.ops_summary.results.load.neighbor.p95_inflation|f2 -->21.80<!-- /num -->배, 429 <!-- num:res.ops_summary.results.load.neighbor.flooder_limited|rpct1 -->80.0% (1200/1500)<!-- /num --> | 오류·429 충족, p95 **빗나감** |
| O16 | judge 장애: 보호 있음의 7번째부터 p95 ≤ judge 없음의 2배, 보호 없음의 중앙값 ≥ judge 지연의 90% | 보호 있음 <!-- num:res.ops_summary.results.load.judge_outage.guarded.after_first6_p95_ms|f2 -->11.81<!-- /num --> ms(judge 없음 <!-- num:res.ops_summary.results.load.judge_outage.no_judge.p95_ms|f2 -->12.69<!-- /num --> ms), 보호 없음 중앙값 <!-- num:res.ops_summary.results.load.judge_outage.unguarded.p50_ms|f2 -->1009.78<!-- /num --> ms | 충족 |

### 9.6 발견과 빗나감의 이유

- **게이트 결함 하나(F-24).** 도전자를 끈 후보가 통과했다. 손실이 오류의 *방향*을 보지 못한 것이고, 안전 방향 손실로 고쳤다([D-15](DECISIONS.md)). 고친 뒤 수치는 수정 후이며 holdout 이 아니다:
  `no_challenge` 차단 <!-- num:hist.ops_shadow_after_safety_loss.results.rows.3.blocked|int -->20<!-- /num -->/<!-- num:hist.ops_shadow_after_safety_loss.results.rows.3.streams|int -->20<!-- /num -->, 나머지 후보의 통과·차단은 첫 접촉과 같다. 개선인데도 크기 제한에 걸려 막히는 쪽(O8)은 그대로다.
- **O10 은 내 계산 착오다.** 20배 번레이트(실패율 2%)가 1시간 창에서 14.4배(1.44%)에 닿으려면 창의 72% 가 장애 구간이어야 한다 — 약 43분. 이것은 정책의 성질이다(짧은 창이 아니라 긴 창이 문턱을 정한다). 임계값 경보(naive)는 같은 장애를 분당 60·600건에서 <!-- num:res.ops_summary.results.alerting.naive.rpm60.partial_degradation_page.delay_median|f2 -->2.00<!-- /num -->분 만에 page 하지만, 분당 5건에서는 장애 없는 구간에 page 오경보가 5개 시계열 합 <!-- num:res.ops_summary.results.alerting.naive.rpm5.false_page|int -->168<!-- /num -->건이다. 빠름과 조용함의 교환이다.
- **R3 발동: 분당 5건에서 burn 이 느린 소모에 page 를 냈다**(<!-- num:res.ops_summary.results.alerting.burn.rpm5.slow_burn_page.alerted|kn -->5/5<!-- /num -->). **가설(확인하지 않았다)**: 요청이 적으면 실패 몇 건이 비율을 크게 흔든다(분당 5건이면 30분 창이 150건 안팎) — 그래서 4배짜리 소모가 가끔 6배 문턱을 넘는 것으로 보인다. 같은 이유로 ticket 오경보도 분당 5건에서 <!-- num:res.ops_summary.results.alerting.burn.rpm5.false_ticket|int -->26<!-- /num -->건이다. 문턱을 이 시계열에 맞춰 조정하지 않았다(R3). 저트래픽 서비스에는 더 긴 창이나 최소 요청 수 조건이 필요하다.
- **O15 의 p95 는 harness 를 탄다.** carol 의 중앙값은 이웃이 쏟아붓는 동안에도 거의 그대로(<!-- num:res.ops_summary.results.load.neighbor.flood_p50_ms|f2 -->9.39<!-- /num --> ms)지만 꼬리가 <!-- num:res.ops_summary.results.load.neighbor.flood_p95_ms|f2 -->342.91<!-- /num --> ms 로 늘었다. 서버와 부하 생성기가 한 프로세스(GIL 공유)라 flood 스레드 32개가 서버 스레드와 CPU 를 다툰 효과가 섞여 있다. 분당 상한이 *정확성*(오류 0, 상한 초과분 429)은 지켰지만 *꼬리 지연*은 한 프로세스 안에서 못 지켰다. 별도 프로세스·노드에서의 격리는 이 리허설이 말하지 못한다.
- **드리프트 검출력의 가파른 문턱.** 이동이 작거나 볼륨이 작으면 system 모니터는 아예 못 본다(위험 소재 급증은 하루 200건에서 이동 비율 0.5 까지도 0/30). 정답 없이 *출력 분포*만 보는 한계다. **가설(확인하지 않았다)**: 위험 소재 급증은 기준 혼합이 이미 위험 64% 라 이동 자체가 작고, 엔진이 내는 반려율·검토율은 정답의 위험 비율과 같은 크기로 움직이지 않는다.
  대출·투자 쏠림처럼 *입력 메타데이터*에 바로 보이는 이동은 하루 200건·비율 0.25 에서도 이틀 안에 잡혔다. 정답 라벨이 늦게 오는 운영에서 label-free 모니터가 있다는 것의 가치와 한계를 둘 다 보여 준다.
