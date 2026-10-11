.PHONY: install test lint type check eval gate sensitivity demo docs judge-replay judge-export judge-check eval-llm e2e clean

PY ?= python
VENV ?= .venv

install:
	$(PY) -m venv $(VENV) && . $(VENV)/bin/activate && pip install -U pip && pip install -e ".[dev]"

lint:
	ruff check src tests

type:
	mypy

test:
	pytest

# 린트·타입·테스트·게이트(합격 기준과 기준선 회귀)를 한 번에
check: lint type test gate

# dev 와 합성 대조군만 돌린다(test·redteam 은 --measure 가 필요하고 기록이 남는다)
eval:
	adgate eval --datasets dev,generated

gate:
	adgate gate

# 구성요소를 일부러 끄고 게이트가 실패하는지 본다(열화 대조군)
sensitivity:
	adgate gate --sensitivity --json-out artifacts/sensitivity.json

# 토큰이 필요하다: ADGATE_DEV=1 이면 데모 토큰(dev-advertiser, dev-reviewer)이 켜진다
demo:
	ADGATE_DEV=1 adgate serve --port 8000

docs:
	adgate docs

# 녹음된 judge 응답을 재생한다(키 불필요, 사전등록: docs/JUDGE_REPLAY.md). 산출물은 var/ 에 써서 커밋된 측정(artifacts/results_judge.json)을 덮지 않는다.
# test·redteam 이 들어 있어 --measure 로 측정 기록(docs/MEASUREMENTS.md)이 한 줄씩 늘어난다.
judge-replay:
	mkdir -p var
	adgate eval --datasets dev,test,redteam --measure --judge recorded --judge-file data/judge_replay/recording.jsonl \
	  --out var/results_judge.json --errors-out var/results_judge_errors.json --note "judge 녹음 재생 재실행"

# 모델에게 보낼 프롬프트 조각을 내보낸다(새로 녹음하려면). 탐지기가 바뀌었으면 data/judge_replay/ 의 녹음은 낡았다.
judge-export:
	adgate judge-export --datasets dev,test,redteam --out-dir var/judge_prompts --shards 9

# 아래 둘은 ANTHROPIC_API_KEY(또는 ant auth login)가 필요하고 비용이 든다
judge-check:
	adgate judge-check --max-usd 0.5

eval-llm:
	adgate eval --datasets dev --judge anthropic --max-usd 2.0 --out artifacts/dev_llm.json --errors-out artifacts/dev_llm_errors.json

# 브라우저 e2e(Chromium 필요): pip install playwright && playwright install chromium
e2e:
	ADGATE_E2E=1 pytest tests/e2e -q

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis var build *.egg-info
