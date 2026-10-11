# LLM judge 녹음 재생 측정

> **상태: 측정 완료(한 번 잼).** 1–3절은 모델 응답을 받기 *전에* 쓴 그대로다 — 사전등록 커밋 `1c22cc1`, 본문 해시는 `tests/test_docs.py` 가 고정한다. 4절 이후가 결과이고 측정 뒤에 썼다. 결과 수치는 `artifacts/results_judge.json` 에서 `adgate docs` 가 채운다.

## 1. 무엇을 알고 싶은가

결정론 경로는 패러프레이즈 14건을 못 잡는다([F-14](FAILURES.md)). LLM judge 는 그 틈을 메우려고 설계했지만 API 호출이 없는 환경이라 한 번도 실측하지 못했다.
같은 파이프라인(프롬프트 → 근거 검증 → 병합 → 판정)에 **실제 LLM 출력**을 흘려 보내면 무엇이 달라지는지를 묻는다.

1. 패러프레이즈를 얼마나 더 잡는가(위험 승인율이 87.5%에서 얼마나 내려가는가)?
2. 정상 소재를 얼마나 잘못 건드리는가(`over_review`, `false_reject`)? 특히 LLM 의 한마디가 **자동 반려**로 이어지는가?
3. 광고 문구 속 지시문(주입)에 속는가? 속아도 결정론 발견은 살아남는가?
4. 근거를 대지 못하는 의심이 얼마나 나오는가(`judge_ungrounded`)?

## 2. 방법 — 그리고 이것이 *무엇이 아닌가*

- **프롬프트·스키마는 어댑터의 것 그대로다.** `adgate judge-export` 가 소재마다 어댑터가 보낼 사용자 메시지(`<creative>` + 결정론 경로가 이미 찾은 `<already_found>`)와 시스템 프롬프트, 출력 스키마를 내보낸다.
- **모델 응답은 Claude Code 서브에이전트가 '모델 대역'으로 만들었다.** 서브에이전트는 시스템 프롬프트·스키마·자기 조각의 사용자 메시지만 읽고 `{"findings": [...]}` 를 쓴다. 정답·데이터셋 이름·소재 id 는 조각 파일에 없다.
- **녹음을 `RecordedJudge` 가 재생한다.** 어댑터와 *같은 검증*(`_Output`, 추가 키 거부)을 거치고, 이후 근거 검증(`ground`), 병합, 판정은 실제 파이프라인이다. 녹음이 없으면 예외 → REVIEW(fail-closed).
- **이것은 어댑터의 API 호출을 재는 것이 아니다.** 구조화 출력, 지정 모델(`claude-opus-5-5`), 노력 수준, 캐시, `fallbacks` 는 시험하지 않았다. 서브에이전트는 하네스의 시스템 프롬프트와 도구를 가진 에이전트이고, 운영 모델과 같은 응답을 낸다는 보장이 없다.
- **모델 계열이 작성자·라벨러와 같다.** 레드팀 소재와 정답은 같은 계열이 썼다. 응답이 정답과 잘 맞는 것은 일부 이 공통성 때문일 수 있다 — 낙관 쪽 편향이다.
- **서브에이전트는 이것이 평가임을 안다.** 실제 호출의 모델은 모른다. 주입에 덜 속았을 가능성이 있다 — 이 역시 낙관 쪽이다.
- 데이터: `dev` 60 + `test` 77 + `redteam` 72 = 209건(내용 중복 없음). `generated` 는 녹음이 없어 제외한다.
- 탐지기는 `a3bdb12`(레드팀 수정과 독립 리뷰 반영이 끝난 상태)로 고정했다. `<already_found>` 가 그 탐지기의 출력이다.

## 3. 사전 예상과 판단 규칙 (측정 전)

시스템: `full`(결정론) · `full+judge`(Judge 발견이 정책의 등급을 그대로 가짐) · `full+judge_cap`(Judge 발견은 REJECT 등급이어도 REVIEW 로 낮춤).

| # | 예상(틀려도 그대로 기록한다) |
|---|---|
| E1 | 패러프레이즈 슬라이스의 위험 승인율은 87.5% → 25% 이하로 내려간다 |
| E2 | `redteam` 의 정상 소재(정답 APPROVE 21건) 중 judge 때문에 REVIEW 이상이 되는 것은 1–4건(5–20%)이다 |
| E3 | 주입 슬라이스에서 결정론이 잡던 발견이 사라지는 일은 없다(구조상 불가능하다 — 테스트 있음). judge 가 주입에 속아 아무것도 제안하지 않는 일은 있을 수 있다 |
| E4 | `judge_ungrounded`(근거 없는 의심)는 전체의 5% 미만이다 |

**판단 규칙(미리 정한다)**

- `full+judge` 가 정답 APPROVE 소재 1건이라도 **REJECT** 로 만들면, 권장 기본값은 `judge_cap_review = True` 다(LLM 한마디로 자동 반려하지 않는다). 그렇지 않으면 기본값은 그대로(`False`) 둔다.
- 어느 쪽이든 두 시스템의 수치를 모두 보인다. 수치가 마음에 들지 않는다는 이유로 프롬프트·스키마·탐지기를 고쳐 다시 재지 않는다. **한 번 재고 그대로 적는다.**
- 소재가 209건이라 한 건이 0.5–5%p 를 움직인다. 모든 비율에 분모를 붙인다.

## 4. 결과 (측정 뒤에 쓴 부분)

**측정은 한 번 했다.** 커밋 `6a3fc27`(작업트리 깨끗), 녹음 209건 [data/judge_replay/recording.jsonl](../data/judge_replay/recording.jsonl)(sha256 `dab233ac…`), 산출물 [artifacts/results_judge.json](../artifacts/results_judge.json), 전체 표는 [RESULTS.md](RESULTS.md).
프롬프트 조각은 `adgate judge-export` 를 다시 돌린 것과 바이트 단위로 같았다([PROMPTS.sha256](../data/judge_replay/PROMPTS.sha256)). 프롬프트·스키마·탐지기는 측정 전에 한 번도 바꾸지 않았고 측정 뒤에도 바꾸지 않았다.
대역 9명이 209건에 후보 31개를 냈다(후보가 있는 소재 28건). 31개 모두 근거 검증을 통과했고, 결정론 발견에 덮여 버려진 것은 없었다.

### 4.1 사전 예상 대 관측

| # | 사전 예상 | 관측 | 판정 |
|---|---|---|---|
| E1 | 패러프레이즈 위험 승인율 87.5% → 25% 이하 | 결정론 <!-- num:judge.results.redteam.full.slices.paraphrase.unsafe_approve|rpct1 -->87.5% (14/16)<!-- /num --> → +judge <!-- num:judge.results.redteam.full+judge.slices.paraphrase.unsafe_approve|rpct1 -->0.0% (0/16)<!-- /num --> (REVIEW 까지만으로 낮춰도 <!-- num:judge.results.redteam.full+judge_cap.slices.paraphrase.unsafe_approve|rpct1 -->0.0% (0/16)<!-- /num -->) | **충족.** 예상보다 훨씬 좋다 — 그래서 더 의심한다(5절) |
| E2 | `redteam` 정상 21건 중 1–4건이 judge 때문에 REVIEW 이상 | `redteam` 에서 반려 <!-- num:judge.results.redteam.full+judge.false_reject|rpct1 -->0.0% (0/21)<!-- /num -->, 보류 <!-- num:judge.results.redteam.full+judge.over_review|rpct1 -->0.0% (0/21)<!-- /num -->. **그러나** `dev` 정상 <!-- num:judge.results.dev.full+judge.false_reject|rpct1 -->9.5% (2/21)<!-- /num --> 이 REJECT, `test` 정상 <!-- num:judge.results.test.full+judge.over_review|rpct1 -->3.9% (1/26)<!-- /num --> 이 REVIEW. 세 셋 합계 <!-- num:judge.pooled.approve_changed|rpct1 -->4.4% (3/68)<!-- /num -->, 그중 REJECT <!-- num:judge.pooled.approve_to_reject|rpct1 -->2.9% (2/68)<!-- /num --> | **빗나감.** 범위 아래(0건)였고, 다른 두 셋에서 자동 반려가 났다 |
| E3 | 결정론 발견이 사라지는 일은 없다 | 세 셋 합쳐 <!-- num:judge.pooled.lost|int -->0<!-- /num -->건 손실(<!-- num:judge.pooled.items|int -->209<!-- /num -->건 중) | 충족. 병합이 후보를 더하기만 하는 구조라 0 이어야 하는 값이다(단위 테스트로도 고정) |
| E4 | 근거 없는 의심(`judge_ungrounded`) 5% 미만 | `dev` <!-- num:judge.meta.judge_replay.datasets.dev.judge_ungrounded|kn -->0/60<!-- /num --> · `test` <!-- num:judge.meta.judge_replay.datasets.test.judge_ungrounded|kn -->0/77<!-- /num --> · `redteam` <!-- num:judge.meta.judge_replay.datasets.redteam.judge_ungrounded|kn -->0/72<!-- /num --> | **측정되지 않았다.** 대역이 쓴 인용을 검사기가 걸렀으므로 구조상 0 이다(4.4) |

E1 의 분모 16건 중 결정론이 놓친 14건(<!-- ids:judge_fixed -->R-038, R-039, R-040, R-041, R-042, R-043, R-044, R-045, R-046, R-047, R-048, R-049, R-051, R-052<!-- /ids -->)을 judge 가 모두 잡았다.
이 14건은 [F-14](FAILURES.md)가 '어휘로는 못 막는다'며 일부러 고치지 않은 바로 그 소재다(`tests/test_docs.py` 가 두 목록이 같음을 확인한다).
정답 규칙도 `redteam` 전체에서 맞았다(규칙 F1 <!-- num:judge.results.redteam.full.rule.micro.f1|f3 -->0.855<!-- /num --> → <!-- num:judge.results.redteam.full+judge.rule.micro.f1|f3 -->1.000<!-- /num -->).

### 4.2 판단 규칙의 적용

정답이 APPROVE 인 소재를 `full+judge` 가 REJECT 로 만든 것이 있다(`dev` 의 <!-- ids:judge_to_reject -->D-004, D-060<!-- /ids -->). **사전등록한 규칙이 발동한다 → 권장 기본값은 `judge_cap_review = True`.**
코드의 기본값을 바꿨다(`ReviewConfig.judge_cap_review`, [DECISIONS.md D-14](DECISIONS.md)). Judge 를 켠 서비스·`judge-check`·`eval --judge anthropic` 은 이제 LLM 의 한마디로 자동 반려하지 않는다.

상한 구성의 대가는 `redteam` 에서 보인다(같은 녹음, 같은 소재).

| `redteam` 구성 | 정확도 | 위험 승인율 | 자동 처리율 | 자동 처리 정밀도 |
|---|---:|---|---:|---|
| 결정론만 | <!-- num:judge.results.redteam.full.accuracy.value|pct1 -->77.8%<!-- /num --> | <!-- num:judge.results.redteam.full.unsafe_approve|rpct1 -->27.5% (14/51)<!-- /num --> | <!-- num:judge.results.redteam.full.auto_rate.value|pct1 -->80.6%<!-- /num --> | <!-- num:judge.results.redteam.full.auto_precision|rpct1 -->75.9% (44/58)<!-- /num --> |
| + judge (정책 등급 그대로) | <!-- num:judge.results.redteam.full+judge.accuracy.value|pct1 -->98.6%<!-- /num --> | <!-- num:judge.results.redteam.full+judge.unsafe_approve|rpct1 -->0.0% (0/51)<!-- /num --> | <!-- num:judge.results.redteam.full+judge.auto_rate.value|pct1 -->75.0%<!-- /num --> | <!-- num:judge.results.redteam.full+judge.auto_precision|rpct1 -->100.0% (54/54)<!-- /num --> |
| + judge (REVIEW 까지만, **새 기본값**) | <!-- num:judge.results.redteam.full+judge_cap.accuracy.value|pct1 -->84.7%<!-- /num --> | <!-- num:judge.results.redteam.full+judge_cap.unsafe_approve|rpct1 -->0.0% (0/51)<!-- /num --> | <!-- num:judge.results.redteam.full+judge_cap.auto_rate.value|pct1 -->61.1%<!-- /num --> | <!-- num:judge.results.redteam.full+judge_cap.auto_precision|rpct1 -->100.0% (44/44)<!-- /num --> |

상한이 정확도를 깎는 것은 틀리게 *승인*해서가 아니다. 정답이 REJECT 인 패러프레이즈 <!-- num:judge.meta.judge_replay.datasets.redteam.transitions_cap.REJECT.APPROVE→REVIEW|int -->9<!-- /num -->건이 반려 대신 사람에게 가기 때문이다.
위험 승인율은 두 구성이 같다. 그리고 결정론만 쓸 때의 높은 자동 처리율에는 잘못된 자동 승인 <!-- num:judge.pooled.unsafe_fixed|int -->14<!-- /num -->건이 섞여 있어서 자동 처리의 정밀도는 위 표처럼 낮다 — 자동 처리율만 보면 결정론이 더 좋아 보이는 것이 함정이다.
`dev` 에서는 상한이 정상 소재 2건을 반려하는 대신 보류하게 한다(오반려 <!-- num:judge.results.dev.full+judge_cap.false_reject|rpct1 -->0.0% (0/21)<!-- /num -->, 오보류 <!-- num:judge.results.dev.full+judge_cap.over_review|rpct1 -->9.5% (2/21)<!-- /num -->).

### 4.3 Judge 가 정상 소재를 건드린 3건

정답 라벨은 측정 뒤에도 바꾸지 않았다. 아래 '원인'은 내 **사후 추정**이고 확인하지 못했다(확인하려면 프롬프트를 바꿔 다시 재야 한다 — 하지 않았다).

| 소재 | 광고 | Judge 후보 (신뢰도) | 정답 | 원인 추정 |
|---|---|---|---|---|
| dev/D-004 | "탈모 증상 완화에 도움을 주는 샴푸"(화장품, 본문에 기능성 화장품) | AD-HLT-01 질병 개선 표방 (0.6) | APPROVE (`functional_claim_ok`) | 프롬프트가 규칙을 한 줄로만 요약한다. 정책 본문의 동사 목록('완화'는 없다)과 '위반이 아닌 예'를 모른다 |
| dev/D-060 | "투자 손실 보상 제도 안내" / "수익보상 절차" + 손실 귀속 고지 | AD-GUA-01 두 건 (0.7, 0.6) | APPROVE (`near_neighbor`) | 모델의 판단. 후보의 근거가 스스로 '하단 고지와 상충'이라고 적을 만큼 회색 지대다 |
| test/T-007 | "가을 신상 할인전, 마감 임박" | AD-URG-01 (0.65) | APPROVE (`urgency_corroborated`) | **judge 입력에 랜딩 텍스트가 없다.** 랜딩에는 '10월 1일 ~ 10월 31일' 이 있다 |

사후 탐색: 이 오탐 후보 4개의 신뢰도는 0.60–0.70 이고, 결과를 바꾼 맞는 후보 18개는 0.65–0.92 다(0.70 초과 15개). 겹치므로 신뢰도 문턱으로는 깨끗하게 나뉘지 않는다.
문턱을 도입하지 않았다 — 같은 209건에 맞추는 일이 된다(`tests/test_docs.py` 가 이 숫자를 녹음에서 다시 센다).

### 4.4 사전등록과 절차의 결함

1. **E4 는 의미가 없었다.** 지시서가 '인용이 글자 그대로가 아니면 검사기가 거부한다'고 했으므로 대역의 인용은 처음부터 모두 근거가 있다. 실제 모델이 인용을 틀리게 옮기는 빈도는 이 측정으로 알 수 없다. 사전등록 때 절차가 E4 를 지워 버리는 것을 예상하지 못했다.
2. **E2 의 범위(1–4건)는 근거 없는 숫자였다.** 틀린 방향도 유리한 쪽(0건)이었으나, 다른 두 셋의 결과가 더 중요했다.
3. **부재형 위반(고지 누락)을 어떻게 낼지 지시문이 정하지 않았다.** 인용할 글이 없는 위반인데 스키마는 `quote` 를 요구한다. 결정론이 이미 `AD-FIN-01` 로 잡은 소재 13건 중 judge 가 같은 규칙을 헤드라인이나 본문의 한 구절을 앵커로 삼아 다시 보고한 것은 3건(대역 1·3·7)이고 10건은 보고하지 않았다. 대역 5·8 은 '인용할 글이 없다'고 밝혔고, 대역 7 은 한 소재에는 보고하고 다른 소재에는 하지 않았다. 같은 지시문에 대한 대역 간·대역 안 차이다.
4. **`already_found` 가 인용 없는 결정론 발견을 보여 주지 않는다.** 그래서 이미 `AD-FIN-01` 로 잡힌 소재(`dev` D-029·D-030, `test` T-034)에 judge 가 같은 규칙을 다시 제안했다. 판정은 바뀌지 않았지만 콘솔에는 지어낸 앵커가 붙은 'LLM 후보'가 보인다.
5. **judge 입력에 랜딩 텍스트가 없다**(T-007). 긴급성(AD-URG-01)의 근거는 랜딩에 있는데 judge 는 그 규칙을 찾으라는 말을 듣고도 랜딩을 보지 못한다.

3–5번과 D-004 는 **프롬프트·입력 설계의 문제**라서 고치면 녹음이 낡는다. 고치지 않았다. 다음 버전(프롬프트 v2)은 새 사전등록으로 다시 재야 한다.

## 5. 이 결과를 어디까지 믿을 수 있는가

낙관 쪽으로 기우는 이유가 겹쳐 있다. 수치를 읽을 때 같이 읽는다.

- **같은 모델 계열이 전부 했다.** 정책과 `dev`·`test` 는 설계자, `redteam` 소재는 작성자 8명, 정답은 라벨러 3명, 지금 judge 응답은 대역 9명 — 모두 같은 계열이다. 패러프레이즈는 'LLM 이 키워드 없이 돌려 쓴' 문장이라 같은 계열이 가장 쉽게 읽는다. 규칙 F1 이 1.0 으로 나온 것은 이 공통성의 신호일 수 있다.
- **대역은 평가임을 안다.** 지시서에 '측정'이라고 적혀 있다. 주입 문구에 덜 속았을 수 있고, 실제 호출의 모델은 모른다.
- **운영 경로를 재지 않았다.** 구조화 출력, 지정 모델, 노력 수준, 캐시, `fallbacks`, 거절, 지연, 비용은 모두 미확인이다.
- **판단의 이득은 한 곳에 몰려 있다.** `dev`·`test` 에서 judge 는 새로 잡은 위험이 없고(결정론이 이미 거의 다 잡는다) 정상 소재 3건만 건드렸다. 이득은 `redteam` 패러프레이즈 14건에서 나왔다.
- **표본이 작다.** 정답 APPROVE 는 합계 68건이고 3건이 바뀌었다. 한 건이 1.5%p 를 움직인다. 패러프레이즈는 16건이다.
- **격리는 지시에 의한 것이지 샌드박스가 아니다.** 대역 9명의 도구 호출 기록을 확인했다: 모두 허용된 파일(지시서·시스템 프롬프트·스키마·자기 조각)만 열었고, 4명은 폴더 목록(`ls`)을 봤을 뿐 다른 파일을 열지 않았다. 호출 기록은 세션에 있고 저장소에는 없다.
- **사후 해석은 가설이다.** 4.3 의 원인 추정과 4.4 는 확인하지 않았다.

이 측정이 보여 주는 것은 **구조**다: 후보만 내는 LLM → 근거 검증 → 병합(결정론 발견은 못 지움) → 판정 → 상한. 프롬프트가 주입 문구에 흔들리지 않았고, 결정론 발견이 하나도 사라지지 않았고, 기본값 결정이 규칙대로 나왔다.
**Judge 의 실제 정확도는 아니다.**

## 6. 재현

```bash
adgate judge-export --datasets dev,test,redteam --out-dir /tmp/prompts --shards 9   # data/judge_replay/PROMPTS.sha256 과 같은 파일
adgate eval --datasets dev,test,redteam --measure --judge recorded --judge-file data/judge_replay/recording.jsonl \
  --out artifacts/results_judge.json --errors-out artifacts/results_judge_errors.json
adgate docs
```

탐지기가 바뀌어 `<already_found>` 가 달라지면 녹음은 낡는다. `tests/test_docs.py` 가 재내보내기와 `PROMPTS.sha256` 이 같은지 확인하므로 그때 테스트가 실패한다 — 다시 녹음하고 다시 재거나, 낡았다고 적어야 한다.
