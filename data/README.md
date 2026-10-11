# 데이터

모든 소재는 **합성**이고 정책은 **가상**이다([docs/POLICY.md](../docs/POLICY.md)). 형식은 정책 문서 7절과 같다(JSON Lines, 한 줄에 소재와 정답).

| 파일 | 누가 썼나 | 언제 | 용도와 규칙 |
|---|---|---|---|
| `dev.jsonl` | 설계자(손작성) | 개발 중 계속 | 규칙을 고칠 때 보는 셋. 감도 분석이 찾은 공백을 메우려고 도전자 사례를 더했다. 자유롭게 본다 |
| `test.jsonl` | 설계자(손작성) | **탐지기 코드보다 먼저**, 커밋 `54ffca0` | 동결. `MANIFEST.sha256` 로 해시를 고정했고 `adgate eval` 은 바뀌었으면 거부한다. 측정은 `--measure` 가 필요하고 [MEASUREMENTS.md](../docs/MEASUREMENTS.md) 에 자동 기록된다 |
| `redteam.jsonl` | 정책 문서만 본 **독립 에이전트** | 탐지기 완성 뒤 첫 측정 | 탐지기·저장소 코드를 보지 못한 상태로 작성. 우회·패러프레이즈·정상 유사·주입 사례. 첫 측정(수정 전) 수치를 보존한다 |
| `labeler_test.jsonl` | 정책 문서만 본 **독립 라벨러** | `test` 동결 뒤 | `test` 소재에 정답을 모르는 채 붙인 판정·위반. 설계자 정답과의 일치도는 [LABELER_AGREEMENT.md](../docs/LABELER_AGREEMENT.md) |
| (생성) | 시드 생성기 | `adgate eval` 이 매번 | `adgate gen --seed 7`. 생성기와 탐지기가 같은 어휘를 공유해 **낙관적**이다. 대조군으로만 쓴다 |

## 정답 라벨의 의미

- `gold.decision` 은 `violations` 의 가장 높은 등급이다(reject > review > approve). 위반이 없으면 `APPROVE`.
- `violations[].quote` 는 해당 필드 원문의 **연속된 부분 문자열**이다. 부재·메타데이터 위반(`AD-ADV-01`, `AD-FIN-01`, 업종 위반)은 `field`·`quote` 가 null 이다.
- `AD-FMT-01`(형식 품질, info)은 정답에 넣지 않는다.
- `tags` 는 슬라이스 분석용이다(`benign_lookalike`, `evasion`, `paraphrase`, `substantiated`, `injection` …).

## 라벨 일관성 검사

`adgate eval` 은 측정 전에 모든 셋을 검사한다: 인용이 원문에 있는가, 규칙 ID 가 정책에 있는가, 판정이 위반 등급 범위 안인가, id 가 유일한가(`tests/test_eval.py`).

## 동결 이후 수정

`test.jsonl` 은 **라벨 오류**에 한정해서만 고친다. 탐지기를 통과시키려는 수정은 하지 않는다. 고치면 `MANIFEST.sha256` 이 바뀌고 이유를 [CHANGELOG](CHANGELOG.md) 에 적는다. 지금까지 수정은 없다.
