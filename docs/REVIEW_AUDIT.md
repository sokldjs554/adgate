# 2026-10-13 심사 흐름 검토 결과

## 범위와 근거

기준 코드 `a383cf2acd5274bea553496ec18fbf8c54e57ef3`. 원격 main을 새로 복제한 작업 공간은 clean이었다. 별도 브랜치에서 작업했으며 사용자 PC의 미커밋 파일은 확인할 수 없다. 원격 main과 기존 결과 브랜치는 수정하지 않는다.

공고 전문을 읽고 [요구사항별 구현 근거](JOB_FIT_ADS.md)를 만들었다. 이번 작업은 광고 심사에 집중했다. 과거 완료 보고·README 숫자는 사실로 가정하지 않고 코드·녹음·산출물·원격 CI와 대조했다.

## 재현한 문제와 수정

| 재현한 문제 | 수정 | 회귀 근거 |
|---|---|---|
| v2도 랜딩을 입력하지 않음. top-k 조항 검색 밖 예외 누락 가능 | v1/v2 그대로 보존, 전문 정책·예외·랜딩·인증 상태를 넣는 v3 분리 | `test_v3_sees_landing_exceptions_metadata_and_absence_findings` |
| 파이프라인 `already_found`가 인용 있는 발견만 전달 | v3만 인용 없는 필수 고지·메타데이터 발견까지 전달; GuardedJudge도 보존 | `test_guarded_v3_receives_absence_context` |
| 랜딩 후보를 검증할 경계가 없음 | 정책에서 허용한 GUA/HLT/BRD만 원문 검증 후 REVIEW. 다른 랜딩 후보는 버리고 근거 부족 이관 | `test_landing_candidates_have_policy_scope_and_review_ceiling_even_without_cap` |
| 다른 역할로 전환해도 기존 소재/결과 DOM이 남음 | 동기적으로 화면·초안·기록 비우기, 이전 인증 응답 무시 | 브라우저 `test_role_switch_clears_previous_owners_result` |
| 서버 저장 후 응답 유실 시 새 키/새 ID로 중복 제출 | 수정 전까지 동일 본문·멱등키 재사용, 저장 결과로 표시 | `test_lost_response_retry_reuses_same_submission`, `test_idempotent_replay_is_labeled_as_saved_not_fresh` |
| 결과 새로고침 복원·원본 재심사 연결·수정안 원본이 없음 | 저장 기록 조회, URL run ID, 소유권 검사한 원본 링크, 수정안 스냅샷 저장 | API audit·reload e2e·실제 재시작 테스트 |
| LLM 후보만 남은 자동 수정 불가 응답의 unresolved가 빈 목록 | 수정 대상과 남은 위반 목록을 분리 | `test_unavailable_rewrite_keeps_llm_only_unresolved_rules` |
| 먼저 선택한 기록의 느린 응답이 나중 선택을 덮음 | 조회 세대 번호로 최신 선택만 반영. 결정 응답도 선택·역할을 검사 | `test_latest_history_selection_wins_over_delayed_response` |

소스·테스트: [API/입력 회귀](../tests/test_review_audit.py), [브라우저](../tests/e2e/test_console.py), [프로세스 재시작](../tests/test_restart_audit.py).

## 평가 설계

[사전 계획](audit/EVAL_REVIEW_20261013.md)을 로컬 커밋 `1ac17eb`에서 등록했다. 코드 변경 전 새 합성 24건을 동결했다. 첫 실행 전 정책 문서 대조로 가격 불일치 한 건의 라벨을 정정했고 [CHANGELOG](../data/audit/CHANGELOG.md)와 원래 커밋을 보존했다.

- dev/test/redteam: 기존 수정에 사용된 회귀셋. 독립 holdout 아님.
- 새 24건: 이번 작성자의 코드 열람 후 작성. 외부 작성자/사람 검수 없음. 독립 holdout 아님.
- LLM 비교: 기존 `data/judge_live/recording.jsonl`의 v1 응답을 같은 코드·같은 소재에 재생. 새 API 호출 없음.
- v3 입력 계약 테스트의 응답 대역은 모델 성능 평가에서 제외했다.
- 정확도는 세 판정의 정확 일치, 위험 승인은 정답 REJECT/REVIEW 중 APPROVE, 정상 오반려는 정답 APPROVE 중 REJECT, 사람 검토율은 전체 중 REVIEW다. 원문 인용 일치는 의미상 판정 정확성과 별도다.

## 동일 조건 비교 (수정 후)

| 데이터/구성 | 정확도 | 위험 승인 | 정상 오반려 | 사람 검토율 |
|---|---:|---:|---:|---:|
| dev 결정론 | <!-- num:res.audit_summary.after.dev.full.accuracy|rpct1 -->95.0% (57/60)<!-- /num --> | <!-- num:res.audit_summary.after.dev.full.unsafe_approve|kn -->0/39<!-- /num --> | <!-- num:res.audit_summary.after.dev.full.false_reject|kn -->0/21<!-- /num --> | <!-- num:res.audit_summary.after.dev.full.human_review|rpct1 -->30.0% (18/60)<!-- /num --> |
| dev v1 녹음+상한 | <!-- num:res.audit_summary.after.dev.full+judge_cap.accuracy|rpct1 -->90.0% (54/60)<!-- /num --> | <!-- num:res.audit_summary.after.dev.full+judge_cap.unsafe_approve|kn -->0/39<!-- /num --> | <!-- num:res.audit_summary.after.dev.full+judge_cap.false_reject|kn -->0/21<!-- /num --> | <!-- num:res.audit_summary.after.dev.full+judge_cap.human_review|rpct1 -->35.0% (21/60)<!-- /num --> |
| test 결정론 | <!-- num:res.audit_summary.after.test.full.accuracy|rpct1 -->100.0% (77/77)<!-- /num --> | <!-- num:res.audit_summary.after.test.full.unsafe_approve|kn -->0/51<!-- /num --> | <!-- num:res.audit_summary.after.test.full.false_reject|kn -->0/26<!-- /num --> | <!-- num:res.audit_summary.after.test.full.human_review|rpct1 -->31.2% (24/77)<!-- /num --> |
| test v1 녹음+상한 | <!-- num:res.audit_summary.after.test.full+judge_cap.accuracy|rpct1 -->98.7% (76/77)<!-- /num --> | <!-- num:res.audit_summary.after.test.full+judge_cap.unsafe_approve|kn -->0/51<!-- /num --> | <!-- num:res.audit_summary.after.test.full+judge_cap.false_reject|kn -->0/26<!-- /num --> | <!-- num:res.audit_summary.after.test.full+judge_cap.human_review|rpct1 -->32.5% (25/77)<!-- /num --> |
| redteam 결정론 | <!-- num:res.audit_summary.after.redteam.full.accuracy|rpct1 -->77.8% (56/72)<!-- /num --> | <!-- num:res.audit_summary.after.redteam.full.unsafe_approve|kn -->14/51<!-- /num --> | <!-- num:res.audit_summary.after.redteam.full.false_reject|kn -->0/21<!-- /num --> | <!-- num:res.audit_summary.after.redteam.full.human_review|rpct1 -->19.4% (14/72)<!-- /num --> |
| redteam v1 녹음+상한 | <!-- num:res.audit_summary.after.redteam.full+judge_cap.accuracy|rpct1 -->83.3% (60/72)<!-- /num --> | <!-- num:res.audit_summary.after.redteam.full+judge_cap.unsafe_approve|kn -->0/51<!-- /num --> | <!-- num:res.audit_summary.after.redteam.full+judge_cap.false_reject|kn -->0/21<!-- /num --> | <!-- num:res.audit_summary.after.redteam.full+judge_cap.human_review|rpct1 -->40.3% (29/72)<!-- /num --> |

상한 없는 LLM 결과도 지우지 않았다: dev에서 정상 오반려 <!-- num:res.audit_summary.after.dev.full+judge.false_reject|kn -->2/21<!-- /num -->. 상한을 쓰면 이를 사람 검토로 보내지만 정확도·자동 처리율의 대가가 생긴다. 표의 구성 간 차이는 **과거 v1 응답의 효과**다.

[before.json](../artifacts/audit/before.json)과 [after.json](../artifacts/audit/after.json)의 지연을 제외한 집계 블록은 동일했다([comparison.json](../artifacts/audit/comparison.json)). 이번 보완으로 정확도가 상승했다고 말하지 않는다. 추가로 기준 main과 수정 소스에서 209건 × 3구성의 전체 결과 JSON을 지연 필드만 제외하고 대조했다. [개별 대조](../artifacts/audit/per_case_comparison.json) 627건 모두 동일했다. 재현 스크립트는 `scripts/compare_review_outputs.py`다.

## 새 데이터 결과와 남은 실패

정확도 <!-- num:res.audit_summary.fresh.accuracy|rpct1 -->87.5% (21/24)<!-- /num -->, 위험 승인 <!-- num:res.audit_summary.fresh.unsafe_approve|rpct1 -->25.0% (3/12)<!-- /num -->, 정상 오반려 <!-- num:res.audit_summary.fresh.false_reject|rpct1 -->0.0% (0/12)<!-- /num -->, 사람 검토 <!-- num:res.audit_summary.fresh.human_review|rpct1 -->8.3% (2/24)<!-- /num -->.

| 실패 | 정답 → 실제 | 해석 |
|---|---|---|
| NEW-021 | REJECT → APPROVE | 미래 잔고가 줄지 않는다는 간접 수익 보장 |
| NEW-022 | REJECT → APPROVE | 병원과 작별한다는 암시적 건강 효능. 라벨 자체에 사람 검수가 필요함 |
| NEW-023 | REVIEW → APPROVE | 랜딩에서 원금 손실이 없다는 완곡한 표현 |

동결 셋을 보고 탐지 어휘를 덧붙이지 않았다. [원문·판정·수정안·v3 입력](../artifacts/audit/fresh.json)을 남겼다. 코드가 플래그한 소재 중 수정안은 검증됨 <!-- num:res.audit_summary.rewrite_status.verified|int -->5<!-- /num -->, 자동 수정 불가 <!-- num:res.audit_summary.rewrite_status.unavailable|int -->4<!-- /num -->. 여기서 검증됨은 재심사·토큰/수치 검사 통과이며 외부 심사자가 의미·사실을 검증한 결과가 아니다. 가격/수치의 단위·대상이 바뀌는 의미 오류는 완전히 검출하지 못한다.

생성된 수정안 5개를 원문과 직접 대조하니 NEW-013의 “성과는 변동될 수 있음 투자로 시작하세요”는 문장이 부자연스럽고, NEW-014의 “조건 없이 승인 여부는 심사 후 결정”은 조건 없다는 표현과 심사 조건이 충돌할 여지가 있었다. 둘 다 자동 검사에서는 verified다. NEW-018의 12,000원/32,000원 불일치는 한쪽 가격을 임의로 택하지 않고 수정 불가로 남았다. 이 관찰은 외부 사람 검수나 사전 정량 품질 평가가 아니며, 해당 동결 셋을 겨냥해 수정기를 튜닝하지 않았다.

## 비용·지연·가용성

- 이번 실행의 API 호출은 <!-- num:res.audit_summary.api_calls_this_run|int -->0<!-- /num -->회. 새 v3 모델의 정확도·비용·지연은 **미측정**이다.
- 이번 새 데이터 CPU 지연은 p50 <!-- num:res.audit_summary.fresh.cpu_latency_ms.p50|ms -->0.46 ms<!-- /num --> / p95 <!-- num:res.audit_summary.fresh.cpu_latency_ms.p95|ms -->2.27 ms<!-- /num -->. 로컬 단일 실행이고 네트워크/API/부하 지연이 아니다.
- 과거 API 기록은 <!-- num:res.audit_summary.existing_live.calls|int -->209<!-- /num -->회, 저장된 추정 비용 <!-- num:res.audit_summary.existing_live.estimated_usd|usd2 -->$1.20<!-- /num -->. 과거 지연 p50/p95는 [JUDGE_LIVE](JUDGE_LIVE.md)와 [호출별 원본](../data/judge_live/calls.jsonl)에 있다. 현재 모델 가용성·가격의 재검증으로 해석하지 않는다.
- 현재 세션에서 API 키·모델·예산 환경변수는 발견되지 않았다. 저장소 규칙에는 Actions secret 사용이 적혀 있지만 비밀값을 읽거나 출력하지 않았다.
- [최근 수동 실행](https://github.com/sokldjs554/adgate/actions/runs/38322660519)의 실제 로그에서 Anthropic credit balance 부족을 확인했다. 지금도 잔액이 같은지는 새 호출하지 않아 모른다. 새 실측에는 유효한 키, 사용 가능한 모델 ID, 최대 예산이 필요하다. 기존 v1 결과는 다시 호출하지 않는다.

## 검증과 실행 재현

```bash
# 기존 응답의 같은 조건 재생 (새 API 호출 없음)
adgate eval --datasets dev,test,redteam --systems full,full+judge,full+judge_cap \
  --judge recorded --judge-file data/judge_live/recording.jsonl --measure \
  --out /tmp/adgate-replay.json --errors-out /tmp/adgate-replay-errors.json --note manual-reproduction
# 새 셋 재실행: 이후 실행은 첫 접촉이 아닌 회귀다. 원본 산출물은 덮어쓰지 않음
python scripts/review_audit.py --out /tmp/adgate-fresh-reproduction.json
# 권한·영속성·브라우저와 경계 검사
pytest tests/test_api.py tests/test_review_audit.py tests/test_restart_audit.py
ADGATE_E2E=1 pytest tests/e2e
ruff check src tests && mypy && pytest
adgate gate && adgate gate --sensitivity && adgate docs --check
```

작업 환경의 SOCKS 프록시 때문에 첫 전체 테스트의 로컬 HTTP 부하 3개가 실패했다. 제품 결함으로 처리하지 않고, 로컬 테스트 프로세스에서만 프록시 환경변수를 제거해 재검증했다. 최초 실패 로그를 보존했다. Chromium 최신 다운로드가 손상된 아카이브를 반환해 Playwright 1.56.0/Chromium 141로 브라우저 검증했다. CSP는 완화하지 않았다.

실제 프로세스 재시작 테스트는 심사·수정안·원본 연결·사람 결정·이벤트 JSON을 종료 전후 비교하고, 저장된 멱등키의 재생과 중복 결정 409까지 확인한다. DB 파일·이벤트는 보존된다. 서버 메모리의 rate limit/관측 지표는 재시작 시 초기화되는 한계가 있다.

기준 커밋의 [기존 CI](https://github.com/sokldjs554/adgate/actions/runs/38271379938)는 success. 수정 브랜치의 최종 CI/검증 상태는 [HANDOFF_WORK](HANDOFF_WORK.md)에 별도로 기록한다.
