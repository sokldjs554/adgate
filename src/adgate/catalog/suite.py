"""라벨링 파이프라인·사람 실험 키트 측정 묶음(docs/EVAL_LABELING.md). 모델 호출이 없다 — ``adgate llm offline catalog`` 가 전부다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..eval import dataset
from ..judge_recorded import RecordedJudge
from ..llm import ScriptedClient
from ..suites import Suite, register
from . import adjudicate, queue, study

DATA = Path(__file__).resolve().parents[3] / "data"
SETS = ("dev", "test", "redteam", "gray")
SIM = study.SimParams(help_when_engine_right=0.05, follow_when_engine_wrong=0.3, speedup=0.8)


def offline() -> dict[str, Any]:
    sets = {n: dataset.load(DATA / f"{n}.jsonl") for n in SETS}
    judge = RecordedJudge.from_file(DATA / "judge_live" / "recording.jsonl")
    pool = [*sets["redteam"], *sets["gray"]]
    items = study.select_items(pool, per_stratum=16, seed=1)
    return {
        "queue": queue.evaluate_queue(sets, judge),
        "adjudication": {f"a{round(a * 100)}": adjudicate.simulate(5000, a, seed=0) for a in (0.8, 0.9, 0.95)},
        "study": {
            "items": {s: sum(i.stratum == s for i in items) for s in study.STRATA}, "unsafe_among_engine_wrong": sum(i.stratum == "engine_wrong" and i.gold != "APPROVE" and i.engine == "APPROVE" for i in items),
            "power": study.power(items, SIM, reviewers=(8, 12, 16, 24), sims=200, seed=0),
        },
    }


@register("catalog")
def catalog() -> Suite:
    return Suite(
        "catalog", "라벨링 우선순위 큐 · 이중 라벨 시뮬레이션 · 사람 실험 키트 검출력(시뮬레이션)", "docs/EVAL_LABELING.md", {}, lambda v: [], lambda v: (lambda p, c: None),
        lambda p: ScriptedClient(lambda t, p2: {}), lambda v, o: {}, offline=offline,
    )


@register("catalog_samples")
def catalog_samples() -> Suite:
    """위험 승인율 추정을 위한 무작위 표집 크기(docs/STUDY_PROTOCOL.md 부록). 새로 재는 것이 아니라 식의 값이다."""
    def offline() -> dict[str, Any]:
        return {f"h{round(h * 100):02d}": {f"p{round(p * 100):02d}": queue.required_n(p, h) for p in (0.05, 0.1, 0.2, 0.3, 0.5)} for h in (0.03, 0.05, 0.1)}

    return Suite("catalog_samples", "무작위 표집 크기(이항 비율 정규 근사)", "docs/EVAL_LABELING.md", {}, lambda v: [], lambda v: (lambda p, c: None), lambda p: ScriptedClient(lambda t, p2: {}), lambda v, o: {}, offline=offline)
