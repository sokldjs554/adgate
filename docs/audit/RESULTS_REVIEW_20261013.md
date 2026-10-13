# 사전 계획에 대한 결과

상세 결과는 [REVIEW_AUDIT](../REVIEW_AUDIT.md), 실행/인계는 [HANDOFF_WORK](../HANDOFF_WORK.md)에 있다.

- 기존 세 데이터셋 × 세 구성의 개별 결과를 지연만 제외하고 대조한 결과는 `artifacts/audit/per_case_comparison.json`에 보존한다.
- 입력/원문/오류/권한/멱등성/재시작/UI 경계는 회귀 시험으로 확인했다.
- v3 모델 실측이 없어 모델 채택 기준은 미평가, 채택 보류다.
- 새 데이터의 위험 승인 3건을 지우거나 이를 겨냥해 규칙을 조정하지 않았다.
