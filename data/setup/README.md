# 셋업 대화 평가셋 (`data/setup/`)

대화형 캠페인 셋업 어시스턴트(`src/adgate/campaign/`)의 평가셋이다. 광고주의 발화가 *정해져* 있고(열린 고리) 정답은 발화마다 추출해야 하는 슬롯과 마지막 상태다.

| 파일 | 내용 |
|---|---|
| `dev.jsonl` | 10건(조각 A·B). 설계자가 프롬프트·정책을 만들며 *보았다* — holdout 이 아니다. 오라클·규칙 추출기 점검에 썼다. |
| `test.jsonl` | 30건(조각 C–H). 설계자는 작성자 보고서(구성 요약과 사양에서 멈칫한 곳)를 읽었지만 시나리오 본문은 측정 전에 열어 보지 않았고, 형식·정합 검사기(`validate_scenarios`)와 구성 집계만 돌렸다. `MANIFEST.sha256` 로 동결. |

- 작성자 8명(조각 A–H × 5건)은 `docs/CAMPAIGN_SPEC.md` 만 보고 썼다. 같은 모델 계열의 별도 컨텍스트 LLM 이다. 사람이 쓴 대화가 아니다.
- 형식은 `src/adgate/campaign/scenarios.py` 의 `Scenario` 이고, 형식·인용·검증기 정합은 `validate_scenarios` 가 검사한다(정답이 *옳은지*는 검사하지 않는다).
- 사양의 공백 26개는 작성자 보고에서 모아 `docs/SETUP_SPEC_GAPS.md` 에 적었다. 정답을 고치지 않았다.
- 모든 인물·업체·URL 은 가상이다(`example.com` 계열).
