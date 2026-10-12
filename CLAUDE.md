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
- LLM judge(`judge_anthropic.py`): 서브에이전트 대역으로 먼저 재고 `docs/JUDGE_REPLAY.md`, 같은 입력으로 실제 모델을 호출해 다시 쟀다 `docs/JUDGE_LIVE.md`(수동 워크플로 `.github/workflows/judge-live.yml`, 녹음은 `data/judge_replay/`·`data/judge_live/`).

## 지금 상태와 다음 할 일

- GitHub `sokldjs554/adgate`(공개)에 `main` 이 올라가 있고 CI 는 통과한다. 태그(`detector-pre-redteam`, `detector-v0`)는 세션의 푸시 권한으로 403 이라 올리지 못했다 → 문서에는 커밋 해시(`f97cc05`, `b50e4c1`)를 함께 적었다.
- **API 키는 GitHub 저장소 비밀값 `ANTHROPIC_API_KEY` 에 있다**(세션 환경에는 없다). 실호출은 Actions 의 `judge-live` 워크플로(수동)로 한다. 결과는 `judge-live-results` 브랜치에 올라오고 `git fetch` 로 가져온다.
- **실호출 측정은 끝났다**(`docs/JUDGE_LIVE.md` 5–6절, 한 번만 쟀다 — 다시 재지 않는다). 다음 실험은 모두 **새 사전등록 문서**로 시작한다(예: `docs/JUDGE_V2.md`).
  후보: ① 프롬프트 v2(규칙 정의·예외, 랜딩 텍스트, 인용 없는 발견을 `already_found` 에, 부재형 위반 처리) ② 신뢰도 문턱 가설(새 셋 또는 v2 에서 문턱을 *미리* 정한다) ③ 반복 호출로 출력 변동, 더 싼 모델 비교. 한 번에 약 $1.2 다.
  비용이 드는 실행은 `--dry-run`/상한을 보여 주고 사용자 승인을 받은 뒤에 한다.
