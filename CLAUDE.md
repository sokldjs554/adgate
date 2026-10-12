# adgate — 에이전트용 작업 규칙

가상 정책 위의 광고 소재 심사 파이프라인이다(전체 소개: README.md). 이 저장소의 가치는 코드보다 **평가 절차**다. 아래 규칙은 그 절차를 지키기 위한 것이다.

## 명령

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
ruff check src tests && mypy && pytest          # CI 와 같은 순서. e2e 는 ADGATE_E2E=1 (Chromium 필요)
adgate gate && adgate gate --sensitivity        # 합격 기준·기준선 회귀·열화 대조군
adgate docs --check                             # 문서의 수치가 artifacts/*.json 과 같은지. 고치려면 `adgate docs`
```

## 지킬 것 (어기면 평가가 의미를 잃는다)

1. **동결 데이터를 고치지 않는다.** `data/test.jsonl`, `data/redteam.jsonl` 은 `data/MANIFEST.sha256` 로 동결돼 있다. 라벨 오류가 확실할 때만 `--allow-modified-frozen` 과 `data/CHANGELOG.md` 기록으로 고친다. 탐지기를 통과시키려는 수정은 하지 않는다.
2. **사전등록 문서의 본문을 고치지 않는다.** `docs/JUDGE_REPLAY.md` 1–3절, `docs/JUDGE_LIVE.md` 1–4절은 측정 *전*에 쓴 것이고 해시를 `tests/test_docs.py` 가 고정한다. 결과는 뒤에 덧붙인다. 예상이 틀려도 고치지 않고 '빗나감'이라 쓴다.
3. **한 번 재고 그대로 적는다.** 수치가 마음에 들지 않는다는 이유로 프롬프트·스키마·탐지기를 고쳐 같은 측정을 다시 하지 않는다. 다른 설정은 새 사전등록으로 한다.
4. **동결 셋 측정은 `--measure`.** `test`·`redteam` 을 돌리면 `docs/MEASUREMENTS.md` 에 한 줄이 자동으로 남는다. 첫 접촉 수치(`artifacts/history/*first_contact*`)는 지우지 않는다. 수정 뒤 수치는 holdout 이 아니라고 표기한다.
5. **문서의 수치는 산출물에서만.** `<!-- num:경로|형식 -->값<!-- /num -->` 표식 안쪽은 손으로 고치지 않는다(`adgate docs` 가 채우고 CI 가 검사한다). 새 수치가 필요하면 산출물에 먼저 넣는다.
6. **예외는 좁게, 악용 테스트와 함께.** 부정문·면책·범위·거래 조건 예외를 넓히거나 좁힐 때마다 '악용' 사례와 정상 유사 사례를 함께 테스트한다(docs/DECISIONS.md D-12). 정규식은 선형 시간이어야 하고 `tests/test_performance.py` 가 증가율을 잰다.
7. **LLM 은 제안만.** 판정은 결정론이다. Judge 출력에 판정 필드를 넣지 않고, 인용이 원문에 없으면 발견이 되지 못하며, 기본은 REVIEW 까지만 낸다(`judge_cap_review=True`, D-14).
8. **키와 비용.** `ANTHROPIC_API_KEY` 는 환경 변수/Actions secret 으로만 쓴다. 채팅·로그·커밋에 넣지 않는다. 실호출(`judge-check`, `judge-record`, `eval --judge anthropic`)은 비용이 드니 `--dry-run`/`--max-usd` 로 상한을 확인하고 사용자 승인 뒤에 한다. CI(`ci.yml`)는 API 를 부르지 않는다.
9. **이 저장소는 공개일 수 있다.** 합성 데이터와 가상 정책만 둔다. 실제 업체 이름·내부 자료·개인 정보·키를 넣지 않는다.

## 어디에 무엇이

- `src/adgate/` 구현, `data/` 데이터와 동결 해시, `artifacts/` 측정 산출물, `docs/` 정책·평가 계획·결정·실패·한계·결과.
- 결정은 `docs/DECISIONS.md`, 틀렸던 것은 `docs/FAILURES.md`, 알려진 한계는 `docs/LIMITATIONS.md`. 새 결정·실패는 거기에 적는다.
- LLM judge: 실호출은 한 번도 못 했던 어댑터(`judge_anthropic.py`). 대리 측정은 `docs/JUDGE_REPLAY.md`, 실호출 측정은 `docs/JUDGE_LIVE.md`(사전등록됨, 수동 워크플로 `.github/workflows/judge-live.yml`).
