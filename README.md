# adgate

[![ci](https://github.com/sokldjs554/adgate/actions/workflows/ci.yml/badge.svg)](https://github.com/sokldjs554/adgate/actions/workflows/ci.yml)

**광고 집행의 전 과정에 LLM 을 붙이고, 만든 순서와 틀린 곳까지 남긴 평가-우선 프로젝트** — 캠페인 셋업 대화 · 소재 생성(문구·이미지) · 심사(원문 인용이 붙은 반려 사유) · 성과 인사이트

> 모든 광고·상품·광고주는 **합성**이고 정책은 **가상**입니다(실제 업체·심사 기준·법령과 무관). 공개된 'AI 광고' 직무 설명을 읽고 만든 개인 프로젝트이며 어느 회사의 구현도 아닙니다.
> 실제 트래픽, 실제 광고주·심사자, 운영 환경에서 얻은 수치는 하나도 없습니다 — 무엇을 못 했는지는 [아래](#검증한-것과-못-한-것)와 [LIMITATIONS.md](docs/LIMITATIONS.md) 에 있습니다.

![반려: 띄어쓰기로 우회한 수익 보장](docs/media/console-reject.png)

## 한눈에

광고주가 말로 목표·예산을 말하면 캠페인이 되고(셋업), 상품 페이지에서 광고 초안이 나오고(생성), 초안이 심사를 받고(심사), 집행 중 성과가 나빠지면 이유와 다음 행동을 알려 줍니다(인사이트).
네 단계가 이어지는 모습은 [통합 데모](docs/DEMO.md)(`adgate demo`)로 한 번에 볼 수 있고, 단계 사이 이음새 10곳을 검사합니다(데모이지 측정이 아닙니다).

**모든 모듈에 같은 원칙이 있습니다 — 모델은 제안하고, 근거가 있을 때만 코드가 받습니다.** 판정과 흐름 제어는 결정론 코드가 합니다. 근거는 원문 인용 · 사실 ID · 코드가 계산한 숫자입니다. 모델이 실패하면 승인하지 않습니다(fail-closed).

| 모듈 | 모델이 하는 일 | 코드가 하는 일 | 첫 접촉에서 잰 것 (합성 데이터, 실호출) | 평가 계획·결과 |
|---|---|---|---|---|
| **심사** `review()` | (선택) 위반 *후보* 제안 | 정규화 · 규칙 12개 · 예외 · 판정 · 사유 · 수정안 재심사 | `redteam` 위험 승인율 <!-- num:history.redteam_v0.redteam.full.unsafe_approve|rpct1 -->41.2% (21/51)<!-- /num --> (내 예상의 거의 세 배) → 고친 뒤 <!-- num:results.redteam.full.unsafe_approve|rpct1 -->27.5% (14/51)<!-- /num --> | [RESULTS](docs/RESULTS.md) · [JUDGE_LIVE](docs/JUDGE_LIVE.md) |
| **캠페인 셋업** `campaign/` | 발화에서 슬롯 후보 추출(인용 필수), 답변 다듬기 | 인용 검사 · 검증기 · 질문 우선순위 · 요약 확인 뒤에만 ready · 상태 전이 가드 | 상태 일치 <!-- num:hist.campaign_sonnet_first_contact.results.test.closed_loop.status_lenient_ok|rpct1 -->86.7% (26/30)<!-- /num --> (같은 모델 단일 호출 <!-- num:hist.campaign_sonnet_first_contact.results.test.single_shot.status_lenient_ok|rpct1 -->63.3% (19/30)<!-- /num -->), 거짓 ready <!-- num:hist.campaign_sonnet_first_contact.results.test.closed_loop.false_ready|rpct1 -->0.0% (0/20)<!-- /num --> | [EVAL_SETUP](docs/EVAL_SETUP.md) |
| **소재 생성** `creative/` | 사실 추출(인용 필수) · 후보 생성 · 수리 | 숨은 글 제거 · 지시 정화 · 사실 심사 · 숫자·브랜드 검증 · 심사 게이트 | 직접 생성은 <!-- num:hist.creative_sonnet_first_contact.results.test.arms.direct.any_leak|rpct1 -->13.9% (5/36)<!-- /num --> 에서 새고 파이프라인(sonnet)은 <!-- num:hist.creative_sonnet_first_contact.results.test.arms.full.any_leak|rpct1 -->0.0% (0/34)<!-- /num --> — 단, 약한 모델(haiku)로 돌리면 <!-- num:hist.creative_haiku_first_contact.results.test.arms.full.any_leak|rpct1 -->12.1% (4/33)<!-- /num --> | [EVAL_CREATIVE](docs/EVAL_CREATIVE.md) |
| **이미지 읽기** `vision/` | 이미지 속 글자·시각 단서 읽기 | 읽기 실패는 승인으로 이어지지 않음 · 읽은 글자는 *데이터*로만 | 이미지 글자를 안 주면 <!-- num:hist.vision_offline_first_contact.results.decision.no_image.accuracy|rpct1 -->30.0% (18/60)<!-- /num -->, 읽어서 주면 <!-- num:hist.vision_sonnet_first_contact.results.decision.read.accuracy|rpct1 -->88.3% (53/60)<!-- /num --> — 세 모델이 같았다(평가가 포화) | [EVAL_VISION](docs/EVAL_VISION.md) |
| **성과 인사이트** `insight/` | 사실에 묶인 리포트 문단 작성 | 같은 요일 기준선 · 이틀 연속 조건 · 신호 서명 진단 · 기대효용 액션 · 리포트 검증 | 이상 재현율 <!-- num:hist.insight_first_contact.results.arms.system.detection.detected|rpct1 -->81.4% (35/43)<!-- /num -->, 이상 없음 오경보 <!-- num:hist.insight_first_contact.results.arms.system.false_alarms.null_scenarios_with_alert|rpct1 -->0.0% (0/20)<!-- /num -->, 해로운 액션 <!-- num:hist.insight_first_contact.results.arms.system.actions.scenarios_with_harmful_action|rpct1 -->0.0% (0/60)<!-- /num --> (대시보드 규칙은 경보의 <!-- num:hist.insight_first_contact.results.arms.naive.actions.harmful|rpct1 -->42.7% (91/213)<!-- /num --> 가 해로운 권고) | [EVAL_INSIGHT](docs/EVAL_INSIGHT.md) |
| 운영 리허설 `ops/` | — | judge 장애 격리 · 드리프트 · 섀도 승격 게이트 · 번레이트 | 섀도 게이트가 정확도 손실로는 열화 후보를 <!-- num:res.ops_summary.results.shadow.no_challenge.allowed|rpct1 -->100.0% (20/20)<!-- /num --> 통과시켰다 → 손실을 안전 방향으로 고침 | [EVAL_OPS](docs/EVAL_OPS.md) |
| 라벨링·사람 실험 `catalog/` | — | 이중 라벨 + 중재 · 사람 실험 키트 | 사전등록한 우선순위 큐가 무작위보다 못했다(AUC <!-- num:hist.catalog_first_contact.results.queue.independent.engine+cues.auc_error|f3 -->0.372<!-- /num -->) → 폐기 | [EVAL_LABELING](docs/EVAL_LABELING.md) |

수치는 `adgate docs` 가 `artifacts/*.json` 에서 채웁니다(손으로 옮겨 적지 않고, CI 가 일치를 검사합니다). **전부 합성 데이터·가상 정책의 수치이며 실제 광고의 정확도가 아닙니다.** 같은 모델 계열이 데이터를 쓰고 정답을 달았다는 한계가 모든 수치에 붙어 있습니다.

## 이 저장소의 핵심은 만든 순서입니다

1. **평가를 먼저 씁니다.** 지표 · 합격 기준 · *내 예상(확률)* · 판단 규칙을 측정 *전에* 쓰고 해시로 고정합니다([사전등록](docs/PREREGISTRATIONS.json)). 예상이 틀려도 고치지 않고 '빗나감'이라 덧붙입니다.
2. **독립 에이전트가 데이터를 씁니다.** 작성자는 정책·사양만 보고 시스템 코드를 보지 못합니다. 평가셋은 해시로 동결하고, 측정 횟수는 도구가 [자동 기록](docs/MEASUREMENTS.md)합니다.
3. **첫 접촉을 한 번만 재고 보존합니다**(`artifacts/history/*first_contact*`). 수치가 마음에 들지 않아 프롬프트·탐지기를 바꿔 같은 측정을 다시 하지 않습니다.
4. **틀린 곳을 부류로 나눠 [FAILURES](docs/FAILURES.md) 에 적고, 고친다면 *검증 가능한 구조*만 좁게 고칩니다.** 수정마다 악용 사례와 정상 유사 사례를 같이 시험하고(D-12), 엔진 변경은 평가셋 전부의 소재 단위 영향을 남깁니다(`adgate impact`, D-18). 수정 뒤 수치에는 '수정 후, holdout 아님'을 답니다.

**잘 안 된 것 (모두 기록돼 있습니다)**

| 무엇이 | 어떻게 | |
|---|---|---|
| 레드팀 첫 접촉이 예상의 세 배 틀렸다 | 위험 승인율 41.2% vs 내 예상 ≤ 15%. 표기 변형만 고치고 뜻을 돌려 말한 14건은 어휘로 막지 않았다 | [F-10](docs/FAILURES.md) · [F-14](docs/FAILURES.md) |
| 내 수정이 만든 회귀를 게이트가 못 잡았다 | 부정문 구멍을 막다 정상 소재를 오반려. 레드팀 재측정이 잡았다 | [F-18](docs/FAILURES.md) |
| 독립 코드 리뷰가 내 수정에서 지수 시간 정규식을 찾았다 | 숫자 26자리에 34초. 증가율 테스트로 바꿨다 | [F-17](docs/FAILURES.md) |
| 승격 게이트가 더 위험한 후보를 모두 통과시켰다 | 정확도 손실은 *방향*을 못 본다 → 안전 방향 비대칭 손실 | [F-24](docs/FAILURES.md) |
| 정답 없는 신호로 만든 큐가 무작위보다 못했다 | 사전등록한 휴리스틱을 버렸다 | [F-26](docs/FAILURES.md) |
| 이미지 읽기 평가가 포화했다 | 어려운 글자를 세 모델이 다 읽었다 — 평가가 모델을 못 갈랐다 | [F-27](docs/FAILURES.md) |
| API 크레딧 소진이 '모델 실패'로 녹음될 뻔했다 | 계정 오류는 기록하지 않고 멈춘다. 그 안전장치도 에이전트의 fail-closed 에 가로막혀 점검이 '성공'했던 것을 고쳤다 | [F-28](docs/FAILURES.md) · [F-33](docs/FAILURES.md) |
| 소재 생성의 안전 지표가 게이트의 일을 못 쟀고, 가격이 모순되는 페이지는 구조가 못 막았다 | 방어가 구조가 아니라 *모델 능력*이었다 — 약한 모델에서 샌다 | [F-29](docs/FAILURES.md) |
| 셋업 어시스턴트가 제한 업종을 '내려 등록'하자는 요청을 받아들였다 | 근거 검사는 '값이 발화에 있는가'만 봤다 → 상태 전이 가드 | [F-30](docs/FAILURES.md) |
| 정답 글자를 줘도 이미지 셋 7건이 틀렸다 | 6건은 문형 공백. 고치면 정상 경고문이 오반려될 뻔해 짝으로 열었다 | [F-31](docs/FAILURES.md) |
| 모델만 쓴 비교 기준이 내 예상보다 강했다 | 구조의 가치는 정확도가 아니라 *해로운 액션 0* 이었다 | [F-32](docs/FAILURES.md) |

## 결과: 심사

수치는 [RESULTS.md](docs/RESULTS.md) 에 전체 표가 있습니다.

| 데이터셋 | 누가 썼고 지금 어떤 상태인가 | N | 정확도 | 위험 승인율 | 정상 오반려 |
|---|---|---:|---|---|---|
| `dev` | 설계자. 규칙을 고칠 때 본 셋 | 60 | <!-- num:results.dev.full.accuracy|rpct1 -->95.0% (57/60)<!-- /num --> | <!-- num:results.dev.full.unsafe_approve|rpct1 -->0.0% (0/39)<!-- /num --> | <!-- num:results.dev.full.false_reject|rpct1 -->0.0% (0/21)<!-- /num --> |
| `test` | 설계자. 탐지기보다 먼저 동결 — **첫 측정 뒤 한 건을 고쳐서 더는 holdout 이 아님** | 77 | <!-- num:results.test.full.accuracy|rpct1 -->100.0% (77/77)<!-- /num --> | <!-- num:results.test.full.unsafe_approve|rpct1 -->0.0% (0/51)<!-- /num --> | <!-- num:results.test.full.false_reject|rpct1 -->0.0% (0/26)<!-- /num --> |
| `redteam` | 정책 문서만 본 독립 작성자 8명 + 독립 라벨러 3명. **수정 뒤 수치라서 holdout 아님** | 72 | <!-- num:results.redteam.full.accuracy|rpct1 -->77.8% (56/72)<!-- /num --> | <!-- num:results.redteam.full.unsafe_approve|rpct1 -->27.5% (14/51)<!-- /num --> | <!-- num:results.redteam.full.false_reject|rpct1 -->0.0% (0/21)<!-- /num --> |
| `gray` | 회색지대를 *의도적으로* 넣은 독립 작성자 10명 + 독립 라벨러 3명. 첫 접촉 뒤 수정 3차까지 — holdout 아님 | 120 | <!-- num:hist.gray_after_vision_fix.results.gray.full.accuracy|rpct1 -->68.3% (82/120)<!-- /num --> | <!-- num:hist.gray_after_vision_fix.results.gray.full.unsafe_approve|rpct1 -->31.4% (22/70)<!-- /num --> | <!-- num:hist.gray_after_vision_fix.results.gray.full.false_reject|rpct1 -->6.0% (3/50)<!-- /num --> |

위험 승인율 = 정답이 반려·보류인 소재를 승인한 비율(낮을수록 좋음). 소재가 적어서 한 건이 1–5%p 를 움직입니다. 구간(Wilson)과 분모는 [RESULTS.md](docs/RESULTS.md).

**수치를 이렇게 읽습니다.**

1. **`test` 의 100% 는 증거가 아닙니다.** 같은 사람이 정책·탐지기·`test` 를 썼고, CI 의 게이트가 매번 읽습니다. 순수 holdout 이었던 것은 *첫 측정*뿐입니다.
2. **`redteam` 첫 접촉은 틀렸습니다.** 위험 승인율 <!-- num:history.redteam_v0.redteam.full.unsafe_approve|rpct1 -->41.2% (21/51)<!-- /num --> — 미리 써 둔 예상(≤ 15%)의 거의 세 배입니다.
   원인을 나눠 *표기를 바꾼* 7건(한자·자모·장식 기호·낱글자 띄우기)만 고쳤고, *뜻을 돌려 말한* 14건("손해 볼 일 없는 투자")은 어휘를 늘려 막지 않았습니다. 평가셋에 맞춘 패치가 되기 때문입니다.
   그래서 지금의 <!-- num:results.redteam.full.unsafe_approve|rpct1 -->27.5% (14/51)<!-- /num --> 가 정확히 그 14건입니다([F-10](docs/FAILURES.md), [F-14](docs/FAILURES.md)).
3. **키워드 블록리스트는 이 문제를 풀지 못합니다.** 같은 `redteam` 에서 정확도 <!-- num:results.redteam.B1_blocklist.accuracy|rpct1 -->26.4% (19/72)<!-- /num -->, 정상 소재 반려 <!-- num:results.redteam.B1_blocklist.false_reject|rpct1 -->57.1% (12/21)<!-- /num -->. 부정문·범위·면책 같은 *예외*가 이 시스템 일의 대부분입니다.
4. **독립 코드 리뷰가 26건의 결함을 찾았습니다.** 그중 하나는 제가 직전에 한 수정이 만든 지수 시간 정규식(숫자 26자리에 34초)이었습니다. 전부 재현해서 회귀 테스트로 남겼습니다([REVIEW.md](docs/REVIEW.md), [F-17](docs/FAILURES.md)).

### LLM judge — 실제 API 로 쟀습니다 (서브에이전트 대역으로 먼저 예측했습니다)

예상과 판단 규칙을 측정 *전에* 커밋하고 한 번씩만 쟀습니다. 먼저 서브에이전트가 '모델 대역'으로 만든 응답 209건을 실제 프롬프트·근거 검증·병합·판정에 재생했고([JUDGE_REPLAY.md](docs/JUDGE_REPLAY.md)),
그다음 **같은 입력으로 실제 `claude-opus-5-5` 를 209번 호출**해 녹음한 뒤 같은 코드로 집계했습니다([JUDGE_LIVE.md](docs/JUDGE_LIVE.md)). GitHub Actions 에서 약 3분, 총 <!-- num:judge_live.info.summary.estimated_usd|usd2 -->$1.20<!-- /num --> 였습니다.

| `redteam` 등 (실호출 기준) | 결정론만 | + judge (정책 등급 그대로) | + judge (REVIEW 까지만, **기본값**) |
|---|---|---|---|
| 패러프레이즈 위험 승인율 | <!-- num:judge_live.results.redteam.full.slices.paraphrase.unsafe_approve|rpct1 -->87.5% (14/16)<!-- /num --> | <!-- num:judge_live.results.redteam.full+judge.slices.paraphrase.unsafe_approve|rpct1 -->0.0% (0/16)<!-- /num --> | <!-- num:judge_live.results.redteam.full+judge_cap.slices.paraphrase.unsafe_approve|rpct1 -->0.0% (0/16)<!-- /num --> |
| 정확도 · 자동 처리율 | <!-- num:judge_live.results.redteam.full.accuracy.value|pct1 -->77.8%<!-- /num --> · <!-- num:judge_live.results.redteam.full.auto_rate.value|pct1 -->80.6%<!-- /num --> | <!-- num:judge_live.results.redteam.full+judge.accuracy.value|pct1 -->97.2%<!-- /num --> · <!-- num:judge_live.results.redteam.full+judge.auto_rate.value|pct1 -->73.6%<!-- /num --> | <!-- num:judge_live.results.redteam.full+judge_cap.accuracy.value|pct1 -->83.3%<!-- /num --> · <!-- num:judge_live.results.redteam.full+judge_cap.auto_rate.value|pct1 -->59.7%<!-- /num --> |
| 정답 APPROVE 중 judge 때문에 바뀐 소재(세 셋 합계) | — | <!-- num:judge_live.pooled.approve_changed|rpct1 -->7.3% (5/68)<!-- /num -->, 그중 REJECT <!-- num:judge_live.pooled.approve_to_reject|rpct1 -->2.9% (2/68)<!-- /num --> | 5건 모두 REVIEW |

- **사전등록한 예상 7개 중 6개가 맞았고 하나(비용)가 빗나갔습니다**(예상 $5–12, 실제 $1.20). 호출 <!-- num:judge_live.info.summary.calls|int -->209<!-- /num -->회에 실패·거절 0건, 제안한 후보 <!-- num:judge_live.info.proposals|int -->76<!-- /num -->개의 인용이 모두 원문과 일치했고(근거 없는 의심 0/209), 지연은 p50 <!-- num:judge_live.info.latency_s.p50|f2 -->2.64<!-- /num -->초 · p95 <!-- num:judge_live.info.latency_s.p95|f2 -->7.04<!-- /num -->초였습니다.
- 대역 측정이 예측한 핵심은 실호출에서도 같았습니다 — 패러프레이즈 0/16, 상한 없으면 정상 소재 2건(`dev` D-004·D-060)이 자동 반려. 달랐던 것은 *양*입니다: 실호출은 후보를 낸 소재가 <!-- num:judge_live.pooled.judge_added|kn -->54/209<!-- /num -->건으로 대역(<!-- num:judge.pooled.judge_added|kn -->28/209<!-- /num -->)의 약 두 배이고, 늘어난 것은 대부분 이미 걸린 소재의 규칙 후보라 규칙 단위 정밀도가 떨어졌습니다.
- 그래서 미리 정한 규칙대로 **Judge 발견은 기본적으로 REVIEW 까지만**(LLM 한마디로 자동 반려하지 않음) 냅니다([D-14](docs/DECISIONS.md)). 정상 소재를 건드린 5건의 후보는 모델 스스로 신뢰도 0.45–0.60 에 '확인이 필요하다'고 쓴 것들이라 상한이 맞는 기본값이고, 대가는 사람 검토 부담입니다.
- 한계: **소재당 한 번 호출**(출력 변동을 모름), 한 모델·한 설정, 소재·정답·라벨러가 같은 모델 계열, 프롬프트 v1 의 알려진 결함(랜딩 텍스트가 입력에 없다 등 — 고치면 다시 재야 해서 고치지 않았습니다, [F-20](docs/FAILURES.md)), 운영 환경이 아닙니다([LIMITATIONS.md](docs/LIMITATIONS.md)).
  프롬프트 v2(정책 조항 검색)와 신뢰도 문턱 가설은 회색지대 셋으로 새 사전등록에 따라 재도록 준비돼 있습니다([EVAL_GRAY.md](docs/EVAL_GRAY.md), [JUDGE_GRAY.md](docs/JUDGE_GRAY.md)).

## 결과: 다른 모듈 (한 줄씩 — 자세한 표와 '빗나감'은 각 평가 문서)

- **캠페인 셋업** — 같은 sonnet 이 슬롯을 뽑는 능력은 단일 호출과 비슷했고(F1 <!-- num:hist.campaign_sonnet_first_contact.results.test.closed_loop.slots.f1|f3 -->0.967<!-- /num --> vs <!-- num:hist.campaign_sonnet_first_contact.results.test.single_shot.slots.f1|f3 -->0.937<!-- /num -->), 차이는 *마지막 상태 판정*에서 났습니다(검증기·요약 확인이 없는 단일 호출은 정답이 ready 인 것의 <!-- num:hist.campaign_sonnet_first_contact.results.test.single_shot.missed_ready|rpct1 -->50.0% (5/10)<!-- /num --> 를 끝내지 못함).
  사전등록한 '금지 값 유입 0'은 빗나갔습니다(haiku 가 성인용품을 생활용품으로 *내려 등록*, sonnet 이 질문 턴의 가정을 상태에 넣음) → 상태 전이 가드를 넣었고 수정 뒤 <!-- num:hist.campaign_sonnet_after_guards.results.test.closed_loop.status_lenient_ok|rpct1 -->90.0% (27/30)<!-- /num --> (**holdout 아님**, [EVAL_SETUP 11절](docs/EVAL_SETUP.md)).
- **소재 생성** — 쌍대 품질 비교에서 파이프라인이 이긴 쌍 <!-- num:hist.creative_sonnet_first_contact.results.test.compare_full_vs_direct.full_wins|rpct1 -->76.5% (26/34)<!-- /num --> vs 직접 생성 <!-- num:hist.creative_sonnet_first_contact.results.test.compare_full_vs_direct.direct_wins|rpct1 -->5.9% (2/34)<!-- /num -->. 그러나 *게이트를 끈 팔도 같은 안전 수치*였습니다 — 지표가 게이트의 일을 못 쟀다는 것을 감사로 확인했습니다(게이트 없는 후보의 <!-- num:hist.creative_sonnet_first_contact_analysis.analysis.gate_audit.nogate.verify_failures|int -->32<!-- /num -->/<!-- num:hist.creative_sonnet_first_contact_analysis.analysis.gate_audit.nogate.candidates|int -->138<!-- /num -->개가 검증 실패, [F-29](docs/FAILURES.md)).
- **이미지 읽기** — 읽기 재현율 <!-- num:hist.vision_sonnet_first_contact.results.reading.text_recall_concat|rpct1 -->100.0% (428/428)<!-- /num --> 로 포화했습니다. 읽는 일이 병목이 아니라 *읽은 글자를 심사하는 엔진*이 병목이었고(정답 글자를 줘도 7/60 오판), 그중 6건을 좁게 고쳐 <!-- num:hist.vision_sonnet_after_fix.results.decision.read.accuracy|rpct1 -->98.3% (59/60)<!-- /num --> 가 됐지만 **고친 소재를 다시 잰 값이라 일반화의 증거가 아닙니다**([F-31](docs/FAILURES.md)).
- **성과 인사이트** — 사실에 묶인 리포트는 검증 <!-- num:hist.insight_llm_sonnet_first_contact.results.writer.verified|rpct1 -->100.0% (43/43)<!-- /num --> 를 통과했지만 '검증 통과'는 '좋은 리포트'가 아닙니다. 도구 없이 표만 본 sonnet 은 내 예상보다 강했고(오경보 <!-- num:hist.insight_llm_sonnet_first_contact.results.raw.null_false_alarm|rpct1 -->0.0% (0/20)<!-- /num -->), 틀린 곳이 시스템과 겹치지 않았으며, 해로운 액션 <!-- num:hist.insight_llm_sonnet_first_contact.results.raw.harmful_actions|rpct1 -->5.0% (3/60)<!-- /num --> 에서 갈렸습니다([F-32](docs/FAILURES.md)).

## 설계 원칙

| 원칙 | 구현 |
|---|---|
| **LLM 은 제안만, 판정은 결정론** | Judge 출력 스키마에 판정 필드가 없다. 제안은 인용이 원문에 있어야 발견이 되고, 기존 발견을 지울 수 없다. 기본은 REVIEW 까지만. Judge 실패는 REVIEW(fail-closed) |
| **모델 출력의 검증은 근거에서 끝나지 않는다** | 값이 발화에 있는가(근거)와 *그 값으로 상태를 바꿔도 되는가*(전이 가드)를 코드가 따로 본다. 제한 업종은 대화로 내려갈 수 없고, 질문·가정 문장의 값은 상태에 들어가지 않는다([D-19](docs/DECISIONS.md)) |
| **인용은 항상 원문의 정확한 조각** | 정규화(NFKC·제로폭·구분 기호·자모)는 원문 오프셋을 보존한다. 파이프라인이 `원문[start:end] == quote` 를 마지막에 검사한다 |
| **애매하면 승인하지 않는다** | 도전자(느슨한 두 번째 검사)는 REVIEW 까지만 낼 수 있다. 주입 문구, 환각 인용, 장애는 모두 안전 쪽으로 기운다 |
| **예외는 좁게, 짝으로, 악용 테스트와 함께** | 부정문·면책·범위·거래 조건은 위반 표현 *바로 옆*일 때만 인정한다. 미탐을 고칠 때는 새로 걸릴 정상 문구를 먼저 찾아 같은 커밋에서 시험한다([D-12](docs/DECISIONS.md), [D-18](docs/DECISIONS.md)) |
| **평가를 먼저 쓰고, 첫 접촉을 보존한다** | 지표와 합격 기준을 구현 전에 [사전등록](docs/EVAL_PLAN.md). `test`·`redteam` 은 해시로 동결하고 측정 횟수를 도구가 [자동 기록](docs/MEASUREMENTS.md). 수정 뒤 수치는 holdout 이 아니라고 표기 |
| **환경 오류는 측정이 아니다** | 크레딧 소진·인증 오류는 녹음에 쓰지 않고 실행을 멈춘다. 멈춤 신호는 운영 코드의 fail-closed 가 가로채지 못하게 분리했다([D-17](docs/DECISIONS.md), [F-33](docs/FAILURES.md)) |

## 빠른 시작

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
make check            # ruff · mypy · pytest · 게이트
make demo             # http://127.0.0.1:8000  (데모 토큰이 켜진 서버)
```

콘솔에서 역할을 고릅니다: 광고주 alice·carol, 심사자 bob. 예시 10개를 불러와 바로 눌러 볼 수 있고, 심사자로 바꾸면 **검토 대기열**이 보입니다.

```bash
adgate review ad.json                       # 한 건 심사(CLI)
adgate demo --print                         # 셋업 대화 → 소재 생성 → 심사 → 성과 진단 → 고리 닫기 (모델 없이 도는 오프라인 전사)
adgate eval --datasets dev,generated        # 평가(test·redteam 은 --measure 가 필요하고 기록이 남는다)
adgate gate --sensitivity                   # 합격 기준·기준선 회귀·열화 대조군(구성요소를 끄면 게이트가 실패해야 정상)
adgate impact snapshot --out before.json    # 엔진을 고치기 전·후의 판정을 소재 단위로 견준다(adgate impact diff before.json after.json)
adgate llm list                             # LLM 측정 묶음(suite) 목록: offline · dry-run · check · record · replay · analyze · spend
adgate docs --check                         # 문서의 수치가 artifacts/*.json 과 같은지
```

모델을 부르는 평가는 **녹음 → 재생**으로 나뉩니다. 실호출은 GitHub Actions(`llm-live`, 저장소 비밀값 `ANTHROPIC_API_KEY`)에서 *손으로만* 돌리고 변형마다 비용 상한을 겁니다. 재생과 집계는 키·비용 없이 깨끗한 작업트리에서 한 번 합니다. CI 는 API 를 부르지 않습니다.
API 는 `/docs`(OpenAPI)에 있습니다. 토큰 없이는 서버가 시작하지 않습니다(`ADGATE_TOKENS` 또는 데모용 `ADGATE_DEV=1`, [.env.example](.env.example)).

## 구조

```
광고주의 말 ─▶ campaign/ ─▶ 사양(슬롯+출처+인용) ─▶ creative/ ─▶ 광고 후보 ─▶ review() ─▶ 판정·사유·수정안 ─▶ 심사자 대기열
(자연어 대화)  셋업 대화                            소재 생성   (게이트 안)    (결정론 엔진)   rewrite() 재심사
                                    상품 페이지·이미지 ─▶ vision/ 이미지 읽기(글자·단서)
집행 중 일별 지표 ─▶ insight/ 이상 탐지 → 원인 진단 → 액션 → 사실에 묶인 리포트 ─▶ "반려 소재 수정" ─▶ rewrite()   (adgate demo)
                    ops/ 운영 리허설(격리·드리프트·섀도·번레이트·부하) · catalog/ 라벨링·사람 실험 · eval/ 평가 하니스·게이트
```

심사 파이프라인(`review()`)은 순수 함수입니다:

```
                    ┌───────────────────────────── review(creative) : 순수 함수 ─────────────────────────────┐
소재 JSON ─▶ 정규화 ─▶ 엄격한 규칙 12개 ─▶ 도전자(변형 표기·필드에 걸친 표현) ─▶ [Judge] ─▶ 병합·판정 ─▶ 사유
 (제출)     (오프셋 보존) (부정·범위·면책·    (REVIEW 까지만)              (선택, 제안만,    (최고 등급)  (규칙 ID·인용·
                         근거 예외, 랜딩 대조)                             근거 검증)                   수정 방법)
                    └──────────────────────────────────────────────────────────────────────────────────────┘
REVIEW ─▶ waiting_input ─▶ 심사자 resume(직무 분리, 한 번만) ─▶ completed          반려/보류 ─▶ 수정안 ─▶ 같은 파이프라인으로 재심사 ─▶ 사실 보존 검사
                              SQLite: runs + 추가 전용 events
```

| 경로 | 내용 |
|---|---|
| `src/adgate/normalize.py` `lexicon.py` `rules.py` `numbers.py` | 정규화, 어휘 패턴, 규칙, 광고-랜딩 수치 대조 |
| `challenge.py` `judge*.py` `pipeline.py` `decide.py` `explain.py` `rewrite.py` | 도전자, Judge 인터페이스·근거 검증·Anthropic 어댑터·녹음 Judge, 파이프라인, 판정, 사유, 수정안 + 재심사 |
| `campaign/` `creative/` `vision/` `insight/` | 셋업 어시스턴트, 소재 생성, 이미지 읽기, 성과 인사이트(각각 `docs/EVAL_*.md` 가 평가 계획과 결과) |
| `llm/` `suites.py` | 공통 LLM 계층(열쇠 · 녹음 · 재생 · 이어하기 · 예산 · 비용 장부 · 멈춤 신호)과 측정 묶음 |
| `ops/` `catalog/` `eval/` `demo.py` | 운영 리허설, 라벨링·사람 실험 키트, 평가 하니스·게이트·영향 점검, 통합 데모 |
| `api.py` `store.py` `settings.py` `web/` | REST API, SQLite, 인증, 콘솔 |
| `data/` | 합성 데이터와 동결 해시, 독립 라벨러 라벨, 녹음(`llm/`·`judge_replay/`·`judge_live/`) ([README](data/README.md)) |
| `docs/` | [구조](docs/ARCHITECTURE.md) · [정책](docs/POLICY.md) · [평가 계획](docs/EVAL_PLAN.md) · [결정](docs/DECISIONS.md) · [실패](docs/FAILURES.md) · [한계](docs/LIMITATIONS.md) · [정책 공백](docs/POLICY_GAPS.md) · [데모](docs/DEMO.md) · [결과](docs/RESULTS.md) |

## 검증한 것과 못 한 것

**확인함**: 단위·속성 기반·API·평가 하니스·수정안·Judge 계약 테스트, 정규식 증가율 테스트(입력 4배 → 시간), 우회 행렬(위반 문구 16종 × 우회 연산자 16종), 독립 리뷰 재현 사례 72개,
악용 + 정상 유사 짝 시험과 *변이 검사*(예외를 일부러 넓히면 악용 시험이 깨지는지), 엔진 변경의 소재 단위 영향(`adgate impact`), 실제 Chromium e2e, 비편집 설치와 토큰 없는 시작 거부, 동시 `resume`·동시 멱등 제출의 경쟁.
**GitHub Actions 에서도 통과합니다**: 파이썬 3.11–3.13 의 ruff · mypy · pytest · 게이트 · 열화 대조군 · 문서 검사, Docker 이미지 빌드·기동·헬스체크·토큰 없는 시작 거부, 브라우저 e2e. 처음 올린 CI 는 3.11 에서 2차 시간 결함을 잡았습니다([F-21](docs/FAILURES.md)).
문서의 수치는 산출물과 같은지 `adgate docs --check` 가 검사하고, 사전등록 본문의 해시도 테스트가 고정합니다.

**못 함**(자세히는 [LIMITATIONS.md](docs/LIMITATIONS.md)):
- **실제 광고주·실제 트래픽·실제 심사자와의 일치도·운영 환경.** 이 저장소의 어떤 수치도 production 에서 나온 것이 아닙니다. 협업(기획자·운영자와 현장에 안착시키기)도 이 저장소가 보일 수 없는 영역입니다.
- 모든 데이터·정답·라벨러가 설계자와 **같은 모델 계열**입니다. 독립 라벨러와의 일치도(`test` <!-- num:agreement.decision_agreement|rpct1 -->98.7% (76/77)<!-- /num -->, `redteam` <!-- num:agreement_redteam.decision_agreement|rpct1 -->100.0% (72/72)<!-- /num -->)는 정책이 명확하다는 증거일 수도, 같은 방식으로 읽는다는 증거일 수도 있습니다.
- **고친 뒤의 수치는 holdout 이 아닙니다.** 고친 구성이 새 독립 소재에서 얼마나 버티는지는 새 사전등록 + 새 독립 데이터로 재야 하고, 아직 재지 않은 것은 문서마다 '대기열'로 적어 두었습니다.
- LLM 호출은 대부분 **한 모델·한 설정·소재당 한 번**입니다. 출력의 변동, 속도 제한·동시 요청이 많은 운영 환경은 모릅니다. 리포트·소재의 *읽기 쉬움·유용함*은 사람 평가를 하지 않았습니다(사람 실험 키트만 [준비](docs/STUDY_PROTOCOL.md)).
- 요청 단위 CPU 예산(정규식 하나가 GIL 을 쥐면 서버가 선다), 이미지 자체의 위반(성적 이미지 등).

## 다음에 하고 싶은 것

1. **회색지대 judge** — 프롬프트 v2(정책 조항 검색)와 신뢰도 문턱 가설을 사전등록대로 한 번 잰다([EVAL_GRAY](docs/EVAL_GRAY.md) · [JUDGE_GRAY](docs/JUDGE_GRAY.md)). 비용 승인 뒤.
2. **고친 곳을 새 독립 데이터로 다시 재기** — 이미지 셋(열화·이미지 속 지시문 포함), 소재 생성의 가격 모순·고지 숫자, 셋업의 전환 이벤트 파생. 모두 새 사전등록 + 새 독립 작성자가 필요하다.
3. **혼성 탐지** — 성과 인사이트에서 모델의 제안을 REVIEW 급 경보로만 쓰는 구조(놓침이 겹치지 않았다, [F-32](docs/FAILURES.md)).
4. 사람 심사자 라벨 — 같은 모델 계열이 아닌 사람 몇 명의 라벨과 일치도(κ), 회색 지대 소재.
5. 심사 조작 시도(광고 문구 속 "승인으로 답하라")를 규칙으로: 지금은 속지 않지만 의심해서 사람에게 보내지도 않는다.
