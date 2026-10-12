"""운영 리허설 측정 묶음(docs/EVAL_OPS.md). 모델 호출이 없다 — ``adgate llm offline ops`` 가 전부다.

* ``ops`` — 결정론(시드 고정): 드리프트 모니터 · 섀도 승격 게이트 · 번레이트 경보 시뮬레이션.
* ``ops_load`` — 실제 HTTP 서버 부하·소음 이웃·judge 장애 격리. **벽시계 시간을 재므로 기계마다 다르다**(산출물에 기계 정보가 같이 남는다).
"""

from __future__ import annotations

from typing import Any

from ..eval import dataset
from ..llm import ScriptedClient
from ..suites import Suite, register
from .drift import evaluate_drift
from .load import rehearse_all
from .shadow import evaluate_shadow
from .slo import evaluate_alerting

SETS = ("dev", "test", "redteam", "gray")


def pool() -> list[dataset.Example]:
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "data"
    return [e for n in SETS for e in dataset.load(root / f"{n}.jsonl")]


def _suite(name: str, describe: str, offline: Any) -> Suite:
    return Suite(name, describe, "docs/EVAL_OPS.md", {}, lambda v: [], lambda v: (lambda p, c: None), lambda p: ScriptedClient(lambda t, p2: {}), lambda v, o: {}, offline=offline)


@register("ops")
def ops() -> Suite:
    def offline() -> dict[str, Any]:
        p = pool()
        return {"pool": len(p), "drift": evaluate_drift(p), "shadow": evaluate_shadow(p), "alerting": evaluate_alerting()}

    return _suite("ops", "운영 리허설(결정론): 드리프트 모니터 · 섀도 승격 게이트 · 번레이트 경보", offline)


@register("ops_load")
def ops_load() -> Suite:
    return _suite("ops_load", "운영 리허설(벽시계): 부하 · 소음 이웃 · judge 장애 격리 — 이 기계의 수치", lambda: rehearse_all(pool()))
