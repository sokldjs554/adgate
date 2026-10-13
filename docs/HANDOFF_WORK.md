# Work 인계: adgate 광고 심사 흐름

검토일 2026-10-13. 이 문서는 자소서나 제출 포트폴리오가 아니라, 작성자가 재사용할 수 있는 코드·실험·실행 근거다. 실제 광고 운영 성과나 지원자의 경력은 저장소만으로 확인하지 않았다.

## 읽는 순서

1. [README](../README.md): 프로젝트 범위, 5분 실행, 승인/반려/사람 검토 사례.
2. [공고 대조](JOB_FIT_ADS.md): 공식 공고 전문을 요구사항별 구현 확인/부분 구현/미구현/검증 불가로 분류.
3. [검토·평가 결과](REVIEW_AUDIT.md): 재현 문제, 같은 조건 비교, 불리한 결과와 분모.
4. [데모 안내](REVIEW_DEMO.md), [실제 실행 영상](media/audit/review-walkthrough.mp4), [원문 근거 화면](media/audit/05-grounding.png).

## 확인한 출발점과 변경 보존

- 원격 main: `a383cf2acd5274bea553496ec18fbf8c54e57ef3`. 새 체크아웃의 브랜치는 main, 미커밋 변경은 없었다. 사용자 PC/이전 세션의 미커밋 변경은 접근할 수 없어 확인하지 못했다.
- 작업은 [feat/ads-review-audit-20261013](https://github.com/sokldjs554/adgate/tree/feat/ads-review-audit-20261013). main과 기존 결과 브랜치는 수정하지 않았다.
- 기존 v1/v2 프롬프트, 녹음, 동결 평가 데이터와 과거 측정은 보존했다. 이전 README도 [원문 보관본](archive/README_BEFORE_20261013.md)에 남겼다. 보관본의 상대 링크는 당시 저장소 루트 기준이다.
- CLI push는 인증 정보가 없어 실패했다. 연결된 GitHub의 git-data API로 별도 브랜치에 동일 Git tree를 저장했다. 커미터 정보 차이로 로컬/원격 커밋 SHA는 다르다. [매핑](../artifacts/audit/commit_map.json)의 tree가 동일함을 확인했으며, 측정 파일의 로컬 SHA를 원격 SHA로 위장하지 않았다.

| 단계 | 로컬 측정/작성 커밋 | 동일 소스의 원격 커밋 |
|---|---|---|
| 사전 계획·새 셋 동결 | `1ac17eb` | [2a45524](https://github.com/sokldjs554/adgate/commit/2a45524cb1c835de02521b40147ef527f749a281) |
| 첫 측정 전 NEW-018 라벨 교정 | `facad52` | [5390534](https://github.com/sokldjs554/adgate/commit/5390534eecf876955ab6ade493322bf18d00aa85) |
| 핵심 코드·회귀 테스트, 수정 후 평가 소스 | `9a9edf2` | [29a8eb5](https://github.com/sokldjs554/adgate/commit/29a8eb5f7a63ee0662efbcefb1fcd37a080ab3e0) |

그 뒤 콘솔의 지연 응답/재생 표시 수정, 실제 프로세스 재시작 테스트, 데모/문서를 추가했다. 캡처 당시 app.js 해시는 [capture.json](media/audit/capture.json)에 있다. 평가 모델과 규칙은 새 셋 결과를 본 뒤 조정하지 않았다.

## 해결한 문제와 설계 결정

| 문제 | 실제 변경과 이유 | 근거 |
|---|---|---|
| 이전 문서의 LLM 정보 부족이 현재도 남아 있는가 | v2에도 랜딩 정보가 없고 top-k 정책 조회가 예외를 놓칠 수 있음을 확인. v1/v2 수정 없이 v3를 추가 | [judge_llm.py](../src/adgate/judge_llm.py), [회귀 시험](../tests/test_review_audit.py) |
| 인용 없는 고지 누락·인증 상태가 모델에 전달되지 않음 | v3에 정책 전문/예외, 랜딩, 인증 상태, 인용 없는 기존 발견을 전달. GuardedJudge도 입력 특성 전달 | [pipeline.py](../src/adgate/pipeline.py), [패키지 정책 원문](../src/adgate/data/policy.reference.v1.md) |
| 후보·근거·최종 판정의 역할 혼동 | 모델은 후보만 제안. 코드가 규칙/업종/원문 위치를 검증. 기본 LLM 후보는 REVIEW 상한. 랜딩 후보는 GUA/HLT/BRD 세 규칙만 허용하며 상한 해제 구성에서도 REVIEW. 오류/근거 부족은 사람 검토 | [judge.py](../src/adgate/judge.py), [config.py](../src/adgate/config.py), 화면 runtime 설명 |
| 역할 전환·늦은 응답으로 이전 소재가 나타남 | 계정 전환 시 DOM/초안 즉시 삭제, 인증 세대와 조회 세대로 응답 적용 제한 | [app.js](../src/adgate/web/app.js), [브라우저 시험](../tests/e2e/test_console.py) |
| 응답 유실 후 중복 제출, 재생을 새 실행으로 표시 | 동일 입력 재시도에 ID/멱등키 재사용. Idempotent-Replayed 헤더를 화면에 반영 | 같은 파일 및 dropped-response 회귀 |
| 수정·재심사·과거 결과 연결이 불명확 | 수정안 전체 스냅샷 저장, GET 복원, 본인 소유 원본만 재심사 연결, URL/기록으로 결과 복원 | [api.py](../src/adgate/api.py), [store.py](../src/adgate/store.py) |
| LLM-only 수정 불가 응답이 미해결 규칙을 숨김 | 자동 편집 가능 대상과 전체 미해결 대상을 분리 | [rewrite.py](../src/adgate/rewrite.py) |
| 새로고침만으로 영속성을 주장할 위험 | 별도 uvicorn 프로세스를 종료/재시작한 뒤 결과·이벤트·수정안·원본 연결·사람 결정 비교 | [test_restart_audit.py](../tests/test_restart_audit.py) |

수정안의 “검증됨”은 원래 숫자/핵심 토큰 검사와 같은 엔진의 재심사 통과를 뜻한다. 숫자가 같아도 단위·대상·문장 의미가 변할 수 있어 사실 보존의 완전한 증명이 아니다. 없는 랜딩 사실을 만들어 자동 승인시키는 흐름으로 설명하지 않는다.

## 평가 조건과 전후 결과

[사전 기준](audit/EVAL_REVIEW_20261013.md): 코드 변경 전 데이터·지표·채택 기준을 기록했다. 새 데이터는 코드 열람 후 같은 작성자가 만든 합성 24건이며 독립 holdout이 아니다. 기존 dev/test/redteam은 이미 개선에 사용된 회귀셋이다. 원문 인용 일치와 의미상 정답은 구분한다.

| 확인한 결과 | 해석 |
|---|---|
| 기존 dev/test/redteam의 결정론·v1 재생·v1 재생+상한: 수정 전후 지연 제외 집계 변화 없음 | 이번 입력/UI 보완의 정확도 향상은 입증하지 못함 |
| redteam 결정론: 정확도 56/72, 위험 승인 14/51, 사람 검토 14/72 | 규칙만으로 완곡한 위반을 놓침 |
| 같은 redteam v1 재생+상한: 정확도 60/72, 위험 승인 0/51, 사람 검토 29/72 | 과거 LLM 응답은 위험 승인을 줄였지만 검토 부담을 늘림. 현재 v3 성능이 아님 |
| dev에서 상한 없는 v1 재생의 정상 오반려 2/21, 상한 적용 후 0/21 | 기본 REVIEW 상한의 근거. 정확도는 결정론 57/60에서 상한 구성 54/60으로 낮아짐 |
| 새 24건 결정론: 정확도 21/24, 위험 승인 3/12, 정상 오반려 0/12, 사람 검토 2/24 | NEW-021/022/023 실패를 보존. 이 결과를 보고 탐지 어휘를 추가하지 않음 |
| 새 셋에서 플래그된 9건 수정안: 검증됨 5, 자동 수정 불가 4 | 모델 재작성/외부 심사자 품질 검증 결과가 아님 |
| 이번 API 호출 0회 | v3 정확도·비용·네트워크 지연은 미측정. 대역 응답을 LLM 결과로 계산하지 않음 |

원본: [before](../artifacts/audit/before.json), [after](../artifacts/audit/after.json), [집계 비교](../artifacts/audit/comparison.json), [개별 결과 대조](../artifacts/audit/per_case_comparison.json), [새 셋 전체 결과](../artifacts/audit/fresh.json), [실패 목록](../artifacts/audit/after_errors.json).

과거 API 209회 기록의 비용 추정 $1.197252 및 지연은 당시 기록이다. 이번 새 셋의 CPU p50 약 0.46ms/p95 약 2.27ms는 로컬 단일 실행으로, API 응답 속도/운영 부하 성능으로 쓰면 안 된다. 비용과 지연의 조건·원본은 [REVIEW_AUDIT](REVIEW_AUDIT.md)에 연결했다.

## 실패한 시도와 남은 한계

- 최신 프롬프트를 입력 계약까지 구현했지만 실제 모델 비교는 못 했다. 사용 가능한 키·모델·예산을 현재 환경에서 찾지 못했다. [이전 실호출 CI](https://github.com/sokldjs554/adgate/actions/runs/38322660519)에는 Anthropic credit 부족 오류가 있었다. 현재 잔액은 재확인하지 않았다.
- 생성된 수정안 직접 대조에서 NEW-013의 부자연스러운 문장, NEW-014에 남은 “조건 없이”와 “심사 후 결정”의 충돌 가능성을 확인했다. 둘 다 자동 verified였으므로 사람 품질 확인은 여전히 필요하다. NEW-018의 가격 불일치는 임의 수정하지 않았다. 새 셋을 겨냥한 튜닝은 하지 않았다.
- 새 셋의 우회적 수익/건강 보장·랜딩 표현을 결정론이 놓쳤다. NEW-022 정답도 독립 사람 검수가 필요하다.
- 첫 전체 테스트는 실행 환경 SOCKS 프록시 의존성 때문에 로컬 HTTP 부하 3개가 실패. 테스트 프로세스에서만 proxy 환경변수를 제거해 통과했다. 최신 Chromium 다운로드 실패 후 Playwright 1.56.0/Chromium 141로 검증했다.
- UI 보완 중 자동 수정 불가 응답에서 잘못된 변수 참조를 브라우저 시험으로 발견해 수정했다. 검토 과정에서 지연 응답 역전과 재생 라벨 오류를 추가로 재현했다. 실패 로그도 보존한다.
- SQLite 파일 보존과 권한 검사는 검증했지만 분산 서버/SSO/실제 광고주 격리 운영은 검증하지 않았다. 동시 중복 요청의 저장은 멱등하지만 요청 중복에 따른 외부 모델 호출 비용까지 single-flight로 막는 것은 아니다.
- 서버 재시작 후 rate limit/메모리 관측 지표는 초기화된다. 미제출 초안과 custom 토큰은 저장하지 않는다.
- 정책은 가상 정책, 데이터는 합성이다. 광고 매출·전환율·심사 처리시간·실제 업무 적용 효과를 측정하지 않았다.
- 공개 호스팅 URL은 없다. 로컬 실행과 실제 녹화 영상으로 검증한다. Docker는 이 작업 컨테이너에서 직접 실행하지 못했고 원격 CI 결과를 별도로 확인한다.

## Work에서 사용해도 되는 표현

| 사용할 수 있는 표현 | 근거가 부족한 표현 |
|---|---|
| “가상 광고 정책으로 제출→근거→수정→재심사→사람 결정 흐름을 구현하고 브라우저/재시작 회귀를 검증했다.” | “토스의 실제 심사를 자동화했다”, “실서비스 운영 경험을 확보했다” |
| “원문 근거와 정책 경계를 코드로 검증하고 LLM 후보를 기본적으로 사람 검토에 제한했다.” | “LLM 환각을 제거했다”, “모든 위험 광고를 막는다” |
| “기존 합성 redteam 72건의 v1 녹음 재생에서 위험 승인 14/51→0/51, 검토율 14/72→29/72였다.” | “최신 v3가 위험 승인을 100% 제거했다”, “실제 광고 정확도 100%” |
| “v3에 정책 전문·예외·랜딩·인용 없는 발견 입력을 보완하고 계약 시험을 추가했다. 모델 효과는 미측정이다.” | “프롬프트 개선으로 정확도/비용이 개선됐다” |
| “새 합성 24건에서 3건 오승인을 공개하고 의미적 사각지대를 확인했다.” | “독립 블라인드 평가를 통과했다” |
| “수정안에 원래 수치 검사와 재심사를 적용했다.” | “수정안의 사실 보존을 보장한다”, “심사자 시간을 N% 절감했다” |

## 재현·검증·CI

설치는 [README](../README.md). 아래 명령은 API를 호출하지 않는다. `--measure` 실행 출력은 새 경로에 저장한다.

```bash
adgate eval --datasets dev,test,redteam --systems full,full+judge,full+judge_cap \
  --judge recorded --judge-file data/judge_live/recording.jsonl --measure \
  --out /tmp/adgate-replay.json --errors-out /tmp/adgate-errors.json --note manual-reproduction
python scripts/review_audit.py --out /tmp/adgate-fresh.json
# 저장된 v1 응답을 사용해 209건 × 3구성 개별 출력(지연 제외)을 비교
# 먼저 저장소의 기준 커밋으로 별도 worktree 생성
# git worktree add --detach /tmp/adgate-baseline a383cf2
python scripts/compare_review_outputs.py --measure --before /tmp/adgate-baseline --out /tmp/adgate-per-case.json
ADGATE_E2E=1 pytest
ruff check src tests
mypy
adgate gate
adgate gate --sensitivity
adgate docs --check
```

로컬 Python 3.12.14, Playwright 1.56.0/Chromium 141. 최종 전체 시험은 **1,405 passed**, 브라우저 14개와 실제 프로세스 재시작 1개를 포함한다. [검증 기록](../artifacts/audit/verification.json)과 [전체 로그](../artifacts/audit/logs/final-tests.log)를 남겼다. SOCKS 환경에서의 실제 명령은 기록 파일을 참조한다.

- 기준 main CI: [38271379938, success](https://github.com/sokldjs554/adgate/actions/runs/38271379938).
- 핵심 수정 커밋 29a8eb5 CI: [38357728417, success](https://github.com/sokldjs554/adgate/actions/runs/38357728417).
- 최종 코드·평가·영상 커밋 [1e16405](https://github.com/sokldjs554/adgate/commit/1e16405b5025e7d04d5acee260e757c1659606c3) (로컬 ad5eb9f) CI: [38358991239, success](https://github.com/sokldjs554/adgate/actions/runs/38358991239). Python 3.11/3.12/3.13 시험·게이트·문서 검증, e2e, Docker 빌드/기동 모두 통과. [조회 결과](../artifacts/audit/remote_ci.json). 이후 커밋은 이 결과와 문장 품질 관찰을 기록한 문서/메타데이터 보충이다.
- [수정 브랜치의 최신 CI 목록](https://github.com/sokldjs554/adgate/actions?query=branch%3Afeat%2Fads-review-audit-20261013).

추가 실측에 필요한 것: 유효한 API 키가 실행 환경에 안전하게 설정되어 있어야 하고, 사용할 수 있는 정확한 모델 ID와 최대 지출 예산이 정해져야 한다. 그 전까지 v3 채택은 보류한다. 실호출 시 v1/v3를 같은 새 입력으로 각각 기록하고 실패·비용·지연·검토부담을 함께 비교해야 한다.
