# Toss AI Engineer (Ads) 공고 대조

확인일: 2026-10-13 (KST). [공식 공고](https://toss.im/career/job-detail?job_id=7990802003).
공식 HTML의 `__NEXT_DATA__`에 포함된 job ID 7990802003의 description 전문을 읽었다. 검색 요약은 업무 일부만 노출하므로 그것으로 요구사항을 확정하지 않았다. 아래는 전문을 요약한 대조표다.
기준 코드 a383cf2, 이번 검토는 feat/ads-review-audit-20261013. 사용자 PC/다른 세션의 현재 브랜치·미커밋 변경은 접근 불가. 새로 복제한 체크아웃은 수정 전 clean main이었다. 원격 결과 브랜치는 보존했다.

표의 구현 확인은 개인 프로젝트 범위다. 공고가 요구하는 실제 사용자·production 운영 경험을 충족했다는 뜻은 아니다.

| 공고 요구사항 요약 | 상태 | 근거 및 판단 |
|---|---|---|
| 광고 정책 위반 탐지·반려 사유·LLM 심사 흐름 | 구현 확인 | [pipeline](../src/adgate/pipeline.py), [judge](../src/adgate/judge.py), [API](../src/adgate/api.py), [재생 평가](../artifacts/audit/after.json). 규칙+선택적 LLM 후보, 검증, REVIEW, 사람이 결정. 사유 생성은 정책 템플릿 |
| 문제 정의 → 구현 → 평가 → 운영 → 반복 개선 | 부분 구현 | [사전 계획](audit/EVAL_REVIEW_20261013.md), [실패](FAILURES.md), [ops](../src/adgate/ops), API/SQLite와 실행 검증. 실제 운영 부분 없음 |
| 자연어 목표·예산 기반 캠페인·전략 구성 | 부분 구현 | [campaign](../src/adgate/campaign), [평가](EVAL_SETUP.md). 멀티턴 슬롯·확인 흐름, 실제 광고 매체 캠페인 생성 연동 없음. 이번 주력 수정 범위 밖 |
| 실제 광고 매체 캠페인 등록·집행 연동 | 미구현 | campaign의 로컬 상태/전략 생성까지만 있으며 매체 API 연결은 없음 |
| 성과 이상 탐지·원인 진단·개선 액션 제시 | 부분 구현 | [insight](../src/adgate/insight), [평가](EVAL_INSIGHT.md). 합성 성과 시계열·근거 기반 보고서. 실제 집행 로그/성과 개선 없음 |
| URL·이미지·텍스트로 소재 생성·추천 | 부분 구현 | [creative](../src/adgate/creative), [vision](../src/adgate/vision), [소재 평가](EVAL_CREATIVE.md). 독립 모듈. 콘솔 이미지 업로드/OCR·전체 생성 플로우 통합 없음 |
| 사용자 입력부터 결과까지 end-to-end 시스템 경험 | 부분 구현 | [실제 화면 영상](media/audit/review-walkthrough.mp4), [브라우저 시험](../tests/e2e/test_console.py). 로컬 가상 사용자 흐름이며 사용자 배포·운영 경험은 별도 |
| LLM으로 실제 문제 해결, 프롬프트/RAG/에이전트/자동화 중 둘 이상 production 운영 | 검증 불가 | 구현과 합성 실험 근거는 있지만 지원자 본인의 production 경험을 저장소로 확인할 수 없음. 개인 프로젝트로 대체했다고 쓰지 않음 |
| 구현 전 브랜드 안전·정책 준수 등 평가 기준 정의 | 구현 확인 | [EVAL_PLAN](EVAL_PLAN.md), [이번 계획](audit/EVAL_REVIEW_20261013.md), 동결 해시·오류 목록·다방향 지표 |
| 모호한 문제를 가설과 최소 실험으로 검증 | 구현 확인 | [DECISIONS](DECISIONS.md), [FAILURES](FAILURES.md), 상한 유무 비교 및 불리한 결과 보존 |
| 확신하는 모델 오류·정책 위반·오사용에 대한 방어 | 구현 확인 | 원문 검증·업종 검사·REVIEW 상한·오류 이관, 역할 경계·멱등성·지연 응답 회귀. 의미 정확성까지 보장하지 않음 |
| 생성형 워크플로 production 운영 (우대) | 검증 불가 | 저장소에 실제 운영·실제 사용자 증거 없음 |
| 생성물 평가셋·회귀 모니터링·검수 체계 (우대) | 구현 확인 | [eval](../src/adgate/eval), [ci](../.github/workflows/ci.yml), [결과](REVIEW_AUDIT.md). 합성 범위 |
| 텍스트·이미지·구조화 데이터 멀티모달 (우대) | 부분 구현 | [vision](../src/adgate/vision), [캠페인 스키마](../src/adgate/campaign). 모듈 실험은 있으나 콘솔 끝까지 연결된 멀티모달 경험은 아님 |
| 에이전틱/멀티턴 설계·운영 (우대) | 부분 구현 | campaign 상태 전이·확인 구현. 실제 운영은 미검증 |
| 콘텐츠 심사·모더레이션 파이프라인 (우대) | 구현 확인 | 본 작업의 주력 범위. 합성 가상정책/로컬 실행으로 한정 |
| 분류·재작성·어노테이션 흐름 (우대) | 부분 구현 | [catalog](../src/adgate/catalog), [라벨링 평가](EVAL_LABELING.md), [rewrite](../src/adgate/rewrite.py). 실제 외부 심사자 라벨 검수는 미실행 |
| 숫자로 제시할 전후 임팩트 | 부분 구현 | [이번 결과](REVIEW_AUDIT.md). 기존 v1 재생 비교, 오류/검토부담. 이번 v3 개선 수치·실제 업무 시간 단축은 없음 |
| 타 직군 협력·현장 안착 경험 | 검증 불가 | 코드·합성 심사 역할은 협업 경험의 증거가 아님. 사용자 본인 확인 필요 |

전형은 서류 이후 기술·ML 시스템 설계 인터뷰를 포함한다. 제출 설명의 초점은 심사 경계의 설계 이유와 실패를 어떻게 확인했는지에 둔다. 공고의 모든 기능을 한 프로젝트에서 완성했다고 주장하지 않는다.
