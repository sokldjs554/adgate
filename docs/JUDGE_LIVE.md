# LLM judge 실호출 측정

> **상태: 사전등록(측정 전).** 이 문서의 1–4절은 **실호출을 하기 전에** 쓰고 커밋한다. 결과(5절 이후)는 측정 뒤에 덧붙이며, 1–4절은 고치지 않는다(본문 해시를 `tests/test_docs.py` 가 고정한다).

## 1. 무엇을 알고 싶은가

[JUDGE_REPLAY.md](JUDGE_REPLAY.md)는 *대역*(서브에이전트)이 만든 응답으로 judge 를 잰 대리 측정이었다. 이번에는 같은 입력을 **실제 API 의 실제 모델**에 넣는다.

1. **어댑터가 실제로 동작하는가.** 요청(구조화 출력, `fallbacks`, `effort`)이 받아들여지는가, 응답이 스키마를 지키는가, 실패(거절·시간 초과·형식 오류)가 얼마나 나는가. 한 번도 실행해 보지 못한 코드다.
2. **대역은 실제 모델의 쓸 만한 대리였는가.** 같은 209건에서 후보를 낸 소재가 얼마나 겹치는가. 대역의 결론(패러프레이즈 0/16, 정상 소재 3/68 변경)이 뒤집히는가.
3. **근거 없는 인용(`judge_ungrounded`)이 얼마나 나는가.** 지난 측정은 검사기가 대역의 인용을 걸러서 이 질문을 지웠다([F-19](FAILURES.md)). 이번에는 모델이 직접 쓴 인용이 그대로 근거 검증을 받는다.
4. **비용과 지연.** 소재 한 건에 얼마가 들고 얼마나 걸리는가.

## 2. 방법

- **입력은 지난 측정과 같다.** `dev` 60 + `test` 77 + `redteam` 72 = 209건, 같은 시스템 프롬프트·출력 스키마·사용자 메시지(`<already_found>` 포함). 프롬프트 조각이 낡지 않았는지는 `tests/test_docs.py` 가 `data/judge_replay/PROMPTS.sha256` 으로 확인한다. 탐지기는 `a3bdb12` 이후 바뀌지 않았다.
- **모델 설정.** `claude-opus-5-5`(어댑터 기본), `effort: medium`, 구조화 출력, `fallbacks: "default"`, 호출 시간 제한 60초, SDK 재시도 1회. 소재마다 한 번 호출한다(동시 4개).
- **기록과 재생이 한 경로다.** `adgate judge-record` 가 응답(또는 실패)을 `data/judge_live/recording.jsonl` 에, 호출별 지연·토큰·종료 사유를 `calls.jsonl` 에, 총량을 `summary.json` 에 남긴다. 집계는 `adgate eval --judge recorded --judge-file data/judge_live/recording.jsonl` — 대역 측정과 **같은 코드, 같은 요약**(`full`, `full+judge`, `full+judge_cap`)이다.
  실패한 호출은 실패 행으로 남고, 재생하면 REVIEW(`judge_unavailable`)가 된다. 실호출과 같은 fail-closed 이고, 실패한 소재가 '판정 없음'으로 사라지지 않는다.
- **대역과의 견줌.** `adgate judge-compare` 가 같은 소재에서 후보를 냈는가(일치/불일치)와 낸 규칙 집합이 같은가를 센다. 실패한 소재는 분모에서 뺀다.
- **키.** 환경 변수 `ANTHROPIC_API_KEY`(또는 Actions secret)로만 넣는다. 코드는 키를 출력하거나 기록하지 않는다. 채팅에 붙여 넣지 않는다.
- **비용 상한.** `--max-usd 12`. 호출 전 `--dry-run` 의 추정은 $5.56(입력은 글자당 토큰 1개, 출력은 호출당 800토큰으로 본 보수적 식)이다. 이 모델은 생각(thinking) 토큰을 쓰므로 실제 비용은 다를 수 있다. 상한에 닿으면 새 호출을 시작하지 않고 멈추며 지금까지의 녹음은 보존된다.
- **첫 호출 점검.** 본 측정 전에 `adgate judge-check`(한 건, 상한 $0.5)로 요청이 받아들여지는지만 본다. 그 한 건은 측정에 넣지 않는다.

## 3. 이 측정이 *아닌* 것

- **소재당 한 번 호출이라 모델 출력의 변동(같은 입력을 다시 넣으면 달라지는 정도)을 모른다.** 한 건의 후보 유무는 우연일 수 있다.
- **모델 하나, effort 하나다.** 더 싼 모델이나 다른 effort 의 비용·정확도 균형은 이 측정이 답하지 않는다.
- **프롬프트 v1 의 알려진 결함을 그대로 둔다**([JUDGE_REPLAY.md 4.4](JUDGE_REPLAY.md)): 랜딩 텍스트가 입력에 없다, 규칙을 한 줄로만 요약한다, `already_found` 가 인용 없는 발견을 빼고, 부재형 위반을 내는 방법이 정해져 있지 않다. 대역과 같은 조건에서 견주려는 것이므로 고치지 않는다. v2 는 새 사전등록으로 잰다.
- **소재·정답·라벨러·대역이 모두 같은 모델 계열이었다.** 이 실호출 모델도 같은 계열일 수 있어 낙관 쪽 편향이 남는다.
- 정답 라벨은 바꾸지 않는다. 프롬프트·스키마·탐지기·모델·effort 는 측정 사이에 바꾸지 않는다.

## 4. 사전 예상과 판단 규칙 (측정 전)

대역 측정 결과를 **이미 안 상태**에서 쓴 예상이다(그래서 대역과 비슷하게 나온다는 쪽으로 기운다). 틀려도 그대로 기록한다.

| # | 예상 |
|---|---|
| L1 | 호출 209건 중 실패(거절·시간 초과·형식 오류·예산)는 5% 미만(10건 이하). 첫 호출 점검에서 요청이 받아들여진다 |
| L2 | `redteam` 패러프레이즈의 위험 승인율(`full+judge_cap`)은 25% 이하(16건 중 4건 이하). 대역(0/16)보다 나쁠 것이다 — 대역은 평가임을 알았다. 놓치는 것은 0–3건으로 본다 |
| L3 | 정답 APPROVE 68건 중 judge 때문에 판정이 바뀌는 소재는 9% 이하(6건 이하)이고, 상한 없이는(`full+judge`) REJECT 가 되는 소재가 1건 이상 있다 |
| L4 | 근거 없는 의심(`judge_ungrounded`)이 붙는 소재는 5% 미만(10건 이하) |
| L5 | 같은 소재에서 후보 유무가 대역과 일치하는 비율이 85% 이상 |
| L6 | 총 비용은 상한 이내이고 $5–12, 호출 지연의 p95 는 30초 이하 |
| L7 | 주입 소재(10건)에서 형식 오류·거절이 없고, 정답이 APPROVE 인 주입 소재(5건)의 판정이 바뀌지 않는다 |

**판단 규칙(미리 정한다)**

- **D1.** `judge_cap_review` 의 기본값(`True`, [D-14](DECISIONS.md))은 이 측정의 결과로 `False` 로 되돌리지 않는다. 상한은 안전 쪽 선택이고, 기본값을 푸는 근거는 오탐이 사라진 다음 버전을 새로 사전등록해 잰 뒤에만 인정한다.
- **D2.** 실패율이 5% 이상이면 어댑터 또는 운영의 문제로 적는다. **기술적 실패**(HTTP 5xx, 시간 초과, 속도 제한)만 같은 설정으로 한 번 이어하기(`judge-record` 재실행)를 허용한다. 거절·형식 오류·판단 결과는 다시 부르지 않는다. 이어하기 전후의 수치를 모두 적는다.
- **D3.** 수치가 마음에 들지 않는다는 이유로 프롬프트·스키마·모델·effort·탐지기를 고쳐 다시 재지 않는다. 다른 설정은 새 사전등록으로 한다.
- **D4.** 예산 상한 때문에 일부 소재만 기록되면, 기록된 것만으로 요약하지 않는다(편향). 상한을 사용자가 올려 이어하거나, 불완전한 측정이라고 적는다.
- 모든 비율에 분모를 붙인다. 소재가 209건이라 한 건이 0.5–5%p 를 움직인다.

## 5. 첫 호출 점검 (본 측정 전, 측정에 넣지 않음)

2026-10-12, GitHub Actions `judge-live` 워크플로(mode=check), [실행 38193224388](https://github.com/sokldjs554/adgate/actions/runs/38193224388), 커밋 `76d6c0c`. 소재는 점검용 한 건(`손해 볼 일 없는 투자`, 평가셋에 없다). 키는 저장소 비밀값이고 로그에도 가려졌다.

- **어댑터가 처음으로 실제 호출에 성공했다.** 구조화 출력(`output_config.format`), `effort: medium`, `fallbacks: "default"`(베타 헤더), 시스템 블록의 `cache_control` 이 모두 받아들여졌다. 사전등록 L1 의 앞부분('첫 호출 점검에서 요청이 받아들여진다')은 충족.
- 지연 5.7초, `stop_reason: end_turn`, 입력 180 · 출력 312 · **캐시 쓰기 2,621 토큰**(시스템 프롬프트가 캐시 가능한 길이를 넘는다 — 표지가 의도대로 동작). 한 건이라 캐시 읽기는 아직 0이다. 추정 비용 $0.020(캐시 쓰기 1.25배 포함).
- 후보 1건: `AD-GUA-01`/`guarantee` 헤드라인 "손해 볼 일 없는 투자", 신뢰도 0.85. 대역이 같은 문구에 낸 후보와 같다. 인용이 원문과 일치해 근거 검증을 통과했고, 기본(상한) 구성에서 결정론 APPROVE → REVIEW 가 됐다.
- 한 건의 사용량으로 총비용을 다시 어림하면(캐시를 읽으면 시스템 프롬프트 비용이 1/10) 209건에 $2–5 쯤이다. dry-run 추정($5.56)과 L6 의 예상($5–12)보다 낮다. 소재마다 길이와 생각 토큰이 달라 한 건으로는 단정할 수 없고, 본 측정의 실제 비용이 답이다.

## 6. 결과 (측정 뒤에 쓴 부분)

**집계는 한 번 했다.** 녹음: Actions [실행 38193673928](https://github.com/sokldjs554/adgate/actions/runs/38193673928)(코드 `42466a3`, 모델 `claude-opus-5-5`, effort `medium`, 소재마다 한 번, 동시 4개). 녹음 파일 sha256 `49c630c8…`, 집계는 그 녹음을 커밋한 `62fa2d5` 의 깨끗한 작업트리에서 `make judge-live-eval` 로 한 번. 산출물은 [artifacts/results_judge_live.json](../artifacts/results_judge_live.json)·[judge_compare.json](../artifacts/judge_compare.json), 전체 표는 [RESULTS.md](RESULTS.md).
프롬프트·스키마·모델·effort·탐지기 판정은 측정 전후에 바꾸지 않았다. 호출은 <!-- num:judge_live.info.summary.calls|int -->209<!-- /num -->회 모두 `end_turn` 으로 끝났다.

### 6.1 예상 대 관측

| # | 사전 예상 | 관측 | 판정 |
|---|---|---|---|
| L1 | 실패 5% 미만, 첫 점검에서 요청이 받아들여진다 | 실패 <!-- num:judge_live.info.summary.failed|int -->0<!-- /num -->건 · 거절 <!-- num:judge_live.info.summary.refusals|int -->0<!-- /num -->건 · 시간 초과 0건(<!-- num:judge_live.info.summary.calls|int -->209<!-- /num -->회 중). 첫 점검(5절)에서 이미 받아들여졌다 | **충족** |
| L2 | `redteam` 패러프레이즈 위험 승인율 25% 이하, 대역(0/16)보다 나쁠 것 | 결정론 <!-- num:judge_live.results.redteam.full.slices.paraphrase.unsafe_approve|rpct1 -->87.5% (14/16)<!-- /num --> → +judge <!-- num:judge_live.results.redteam.full+judge_cap.slices.paraphrase.unsafe_approve|rpct1 -->0.0% (0/16)<!-- /num -->(상한 없이도 <!-- num:judge_live.results.redteam.full+judge.slices.paraphrase.unsafe_approve|rpct1 -->0.0% (0/16)<!-- /num -->) | 앞 절반 **충족**. '대역보다 나쁠 것'은 **빗나감** — 대역과 같다 |
| L3 | 정답 APPROVE 68건 중 바뀌는 것 9% 이하, 상한 없이는 REJECT 가 1건 이상 | 세 셋 합계 <!-- num:judge_live.pooled.approve_changed|rpct1 -->7.3% (5/68)<!-- /num -->, 그중 REJECT <!-- num:judge_live.pooled.approve_to_reject|rpct1 -->2.9% (2/68)<!-- /num --> | **충족.** 대역(3건)보다 2건 많다 |
| L4 | 근거 없는 의심 5% 미만 | <!-- num:judge_live.pooled.ungrounded|rpct1 -->0.0% (0/209)<!-- /num -->. 제안한 후보 <!-- num:judge_live.info.proposals|int -->76<!-- /num -->개 모두 인용이 원문과 글자까지 일치해 근거 검증을 통과했다 | **충족.** 대역 측정이 지웠던 질문([F-19](FAILURES.md))에 처음으로 답한다 |
| L5 | 같은 소재에서 후보 유무가 대역과 85% 이상 일치 | <!-- num:judge_live.compare.agreement|rpct1 -->87.6% (183/209)<!-- /num -->. 불일치는 전부 한 방향이다: 실호출만 후보를 낸 소재 <!-- num:judge_live.compare.only_b|int -->26<!-- /num -->건, 대역만 낸 소재 <!-- num:judge_live.compare.only_a|int -->0<!-- /num -->건 | **충족**, 그러나 6.3 |
| L6 | 총 비용 $5–12(상한 이내), 지연 p95 30초 이하 | 총 <!-- num:judge_live.info.summary.estimated_usd|usd2 -->$1.20<!-- /num -->(상한 $6), 지연 p50 <!-- num:judge_live.info.latency_s.p50|f2 -->2.64<!-- /num -->초 · p95 <!-- num:judge_live.info.latency_s.p95|f2 -->7.04<!-- /num -->초 · 최대 <!-- num:judge_live.info.latency_s.max|f2 -->9.82<!-- /num -->초 | 지연 **충족**, 비용은 **빗나감**(예상보다 훨씬 쌌다) |
| L7 | 주입 소재(10건)에서 형식 오류·거절이 없고, 정답이 APPROVE 인 주입 소재(5건)의 판정이 바뀌지 않는다 | 오류·거절 0건, 주입 APPROVE 소재의 정확도 <!-- num:judge_live.results.redteam.full+judge.slices.injection_only.accuracy|rpct1 -->100.0% (5/5)<!-- /num --> | **충족** |

일곱 중 여섯을 충족했고 비용이 빗나갔다. 'L2 의 대역보다 나쁠 것'도 틀렸다. 대역 측정의 핵심 결론 — 패러프레이즈 <!-- num:judge_live.results.redteam.full+judge.slices.paraphrase.unsafe_approve|rpct1 -->0.0% (0/16)<!-- /num -->, 정상 소재 2건이 상한 없이 REJECT(`dev` D-004·D-060) — 은 실호출에서도 **그대로** 나왔다.

| `redteam` 구성 | 정확도 | 위험 승인율 | 자동 처리율 | 자동 처리 정밀도 |
|---|---:|---|---:|---|
| 결정론만 | <!-- num:judge_live.results.redteam.full.accuracy.value|pct1 -->77.8%<!-- /num --> | <!-- num:judge_live.results.redteam.full.unsafe_approve|rpct1 -->27.5% (14/51)<!-- /num --> | <!-- num:judge_live.results.redteam.full.auto_rate.value|pct1 -->80.6%<!-- /num --> | <!-- num:judge_live.results.redteam.full.auto_precision|rpct1 -->75.9% (44/58)<!-- /num --> |
| + judge (정책 등급 그대로) | <!-- num:judge_live.results.redteam.full+judge.accuracy.value|pct1 -->97.2%<!-- /num --> | <!-- num:judge_live.results.redteam.full+judge.unsafe_approve|rpct1 -->0.0% (0/51)<!-- /num --> | <!-- num:judge_live.results.redteam.full+judge.auto_rate.value|pct1 -->73.6%<!-- /num --> | <!-- num:judge_live.results.redteam.full+judge.auto_precision|rpct1 -->100.0% (53/53)<!-- /num --> |
| + judge (REVIEW 까지만, 기본값) | <!-- num:judge_live.results.redteam.full+judge_cap.accuracy.value|pct1 -->83.3%<!-- /num --> | <!-- num:judge_live.results.redteam.full+judge_cap.unsafe_approve|rpct1 -->0.0% (0/51)<!-- /num --> | <!-- num:judge_live.results.redteam.full+judge_cap.auto_rate.value|pct1 -->59.7%<!-- /num --> | <!-- num:judge_live.results.redteam.full+judge_cap.auto_precision|rpct1 -->100.0% (43/43)<!-- /num --> |

결정론이 놓친 위험 소재 14건(<!-- ids:live_fixed -->R-038, R-039, R-040, R-041, R-042, R-043, R-044, R-045, R-046, R-047, R-048, R-049, R-051, R-052<!-- /ids -->)을 실호출도 모두 잡았다. 대역이 잡은 것과 같은 14건이고, [F-14](FAILURES.md)가 '어휘로는 못 막는다'며 남겨 둔 바로 그 소재다.

### 6.2 판단 규칙의 적용

- **D1.** `judge_cap_review = True` 를 유지한다. 상한 없이 정답 APPROVE 소재가 REJECT 가 되는 일이 실호출에서도 있었다(<!-- ids:live_to_reject -->D-004, D-060<!-- /ids -->). 기본값을 되돌리는 근거는 나오지 않았다.
- **D2.** 기술적 실패가 0건이라 이어하기를 하지 않았다.
- **D3.** 프롬프트·스키마·모델·effort 를 고쳐 다시 재지 않았다. 탐지기 코드는 [F-21](FAILURES.md)의 성능 수정만 있었고 판정은 같다(`tests/test_docs.py` 가 녹음이 낡지 않았음을 확인한다).
- **D4.** 예산 상한($6)에 닿지 않았고 209건이 모두 기록됐다.

### 6.3 대역과 달랐던 것 — 실호출이 더 많이 말한다

- 후보를 낸 소재가 대역 <!-- num:judge.pooled.judge_added|kn -->28/209<!-- /num --> 대 실호출 <!-- num:judge_live.pooled.judge_added|kn -->54/209<!-- /num -->(제안한 후보는 <!-- num:judge.pooled.judge_findings|int -->31<!-- /num -->개 대 <!-- num:judge_live.info.proposals|int -->76<!-- /num -->개이고, 그중 실호출의 <!-- num:judge_live.info.merged_away|int -->5<!-- /num -->개는 결정론이 같은 자리를 이미 같거나 더 무겁게 판정해 병합에서 빠졌다)이다. 대역이 낸 <!-- num:judge_live.compare.agree_nonempty|int -->28<!-- /num -->건은 실호출도 모두 냈다. 낸 규칙 집합까지 같은 것은 그중 <!-- num:judge_live.compare.rule_sets_equal|int -->24<!-- /num -->건이다.
- 실호출만 후보를 낸 26건 중 23건은 정답이 REJECT/REVIEW 라서 **판정이 바뀌지 않았다**. 이미 결정론이 잡은 소재에 규칙 후보를 더 얹은 것이다(부재형 `AD-FIN-01` 재제안, 같은 표현의 다른 규칙 등). 규칙 단위 정밀도가 그만큼 떨어진다: `redteam` 규칙 F1 은 대역 1.000 → 실호출 <!-- num:judge_live.results.redteam.full+judge.rule.micro.f1|f3 -->0.966<!-- /num -->.
- 나머지 3건 중 둘은 **정상 소재를 건드렸다**: `dev` D-011("자격증 합격 후기 모음", ENDO, REVIEW), `redteam` R-010("마감 임박" 캠핑장, URG, REVIEW). 하나는 판정을 맞게 올렸다(`dev` D-059 REVIEW → REJECT).
  정상 소재를 건드린 5건의 정체: 상한 없이 REJECT 2건(D-004 HLT, D-060 GUA), REVIEW 3건(D-011, T-007, R-010).
- **모델이 스스로 확신하지 못했다.** 정상 소재를 건드린 다섯 소재의 후보 신뢰도는 0.45–0.60 이다. 근거 문장도 '랜딩 페이지에서 확인해야 한다'(T-007·R-010), '실제 기능성 심사 여부를 사람이 확인해야 한다'(D-004)처럼 *확인 요청*이다. 스키마가 규칙마다 한 가지 심각도만 허용해서 '확인해 보라'가 REJECT 등급 후보(HLT)가 되는 것이 상한 없는 구성의 문제다.

### 6.4 새로 알게 된 것

1. **인용 정확도**: 제안한 후보 <!-- num:judge_live.info.proposals|int -->76<!-- /num -->개 전부 원문과 일치했다. 대역 측정에서는 알 수 없던 값이다. (표본이 76개라 '틀리지 않는다'가 아니라 '이 입력에서는 틀리지 않았다'이다.)
2. **비용과 지연**: 209건에 <!-- num:judge_live.info.summary.estimated_usd|usd2 -->$1.20<!-- /num -->, 호출당 p50 <!-- num:judge_live.info.latency_s.p50|f2 -->2.64<!-- /num -->초. 시스템 프롬프트 2,621토큰은 점검 호출이 쓴 캐시를 209번 모두 읽었다(캐시 읽기 <!-- num:judge_live.info.summary.cache_read_tokens|num -->547,789<!-- /num -->토큰, 쓰기 <!-- num:judge_live.info.summary.cache_write_tokens|int -->0<!-- /num -->). 한 번 재는 데 $1.2 이면 프롬프트 v2·다른 모델·반복 호출을 새 사전등록으로 재 볼 만하다.
3. **지난 사후 추정이 맞았다**: 정상 소재 오탐의 한 원인은 입력에 랜딩 텍스트가 없는 것이다([JUDGE_REPLAY.md 4.3](JUDGE_REPLAY.md)). 이번엔 모델이 근거 문장에 그렇게 적었다(T-007, R-010). 확정은 아니다 — 프롬프트를 바꿔 재야 한다.
4. **사후 탐색(채택하지 않음)**: 대역에서는 신뢰도가 오탐과 겹쳤지만, 실호출에서는 정상 소재를 바꾼 5건의 소재별 최대 신뢰도가 0.45–0.60 이고, 위험을 잡아 판정을 바꾼 17건은 0.60(1건)·0.70(1건)·0.75 이상이다. 문턱(예: 0.65)이 이 데이터에서는 잘 나눌 듯하지만 **같은 209건에 맞춘 값이라 채택하지 않았다**(D1·D3). 프롬프트 v2 와 함께 새 사전등록에서 시험할 가설이다. (`tests/test_docs.py` 가 이 숫자를 녹음에서 다시 센다.)

### 6.5 이 결과를 어디까지 믿을 수 있는가

- **소재당 한 번 호출했다.** 모델 출력의 변동을 모른다. 후보 유무가 갈리는 경계 사례(신뢰도 0.4–0.6)는 다시 부르면 달라질 수 있다.
- **모델 하나, effort 하나, 프롬프트 v1(알려진 결함 포함)이다.** 더 싼 모델이나 다른 effort 의 균형은 모른다.
- **소재·정답·라벨러가 같은 모델 계열이다.** 이 모델이 같은 계열이면 낙관 쪽 편향이 남는다.
- **정답 APPROVE 68건 중 5건이 바뀌었다.** 한 건이 1.5%p 이다. `dev`·`test` 는 설계자가 쓴 셋이다.
- **운영 환경이 아니다.** GitHub 러너에서 동시 4개로 3분 걸렸다. 속도 제한, 긴 소재, 동시 요청이 많은 상황은 재지 않았다.
- 그래도 **대역 측정의 방법**(같은 프롬프트·근거 검증·병합을 서브에이전트 응답에 적용)은 핵심 결론을 맞게 예측했다 — 패러프레이즈 0/16, 정상 소재 REJECT 2건. 틀린 것은 *양*이다: 실호출이 후보를 약 두 배 더 낸다.
