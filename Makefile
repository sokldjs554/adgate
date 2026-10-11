.PHONY: install test lint type check eval gate sensitivity demo docs judge-check eval-llm e2e clean

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
