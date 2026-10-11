"""게이트: 사전등록한 합격 기준(docs/EVAL_PLAN.md 4절)과 커밋된 기준선 대비 악화를 검사한다.

열화 대조군(sensitivity): 구성요소를 일부러 끈 파이프라인으로 같은 게이트를 돌려 *실패해야* 정상이다.
실패하지 않는 구성요소는 평가셋이 그것을 덮지 못한다는 뜻이므로 gap 으로 보고한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import ReviewConfig
from . import metrics
from .dataset import Example
from .systems import System, naive_blocklist, pipeline_system

TOLERANCE = 0.005
# 기준선과 비교할 지표: (이름, 경로, 클수록 좋은가)
TRACKED: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("accuracy", ("accuracy", "value"), True),
    ("unsafe_approve", ("unsafe_approve", "value"), False),
    ("false_reject", ("false_reject", "value"), False),
    ("over_review", ("over_review", "value"), False),
    ("auto_rate", ("auto_rate", "value"), True),
    ("rule_f1", ("rule", "micro", "f1"), True),
)


@dataclass
class Check:
    id: str
    desc: str
    ok: bool
    value: Any
    threshold: Any
    kind: str  # required | target | regression


def _get(m: dict[str, Any], path: tuple[str, ...]) -> Any:
    cur: Any = m
    for p in path:
        cur = cur[p]
    return cur


def acceptance(m: dict[str, Any], b1: dict[str, Any] | None = None) -> list[Check]:
    """사전등록 합격 기준. m 은 `test` 에서의 지표, b1 은 같은 셋에서의 순진한 블록리스트 지표."""
    checks = [
        Check("H1", "hard_pass_through = 0", m["hard_pass_through"]["k"] == 0, m["hard_pass_through"]["k"], 0, "required"),
        Check("H2", "grounding = 100% (측정 대상이 있어야 한다)", m["grounding"]["value"] == 1.0, m["grounding"]["value"], 1.0, "required"),
        Check("H3", "unsafe_approve ≤ 3%", (m["unsafe_approve"]["value"] or 0) <= 0.03, m["unsafe_approve"]["value"], 0.03, "required"),
        Check("S1", "false_reject ≤ 5%", (m["false_reject"]["value"] or 0) <= 0.05, m["false_reject"]["value"], 0.05, "target"),
        Check("S2", "over_review ≤ 10%", (m["over_review"]["value"] or 0) <= 0.10, m["over_review"]["value"], 0.10, "target"),
        Check("S3", "auto_rate ≥ 70%", (m["auto_rate"]["value"] or 0) >= 0.70, m["auto_rate"]["value"], 0.70, "target"),
        Check("S4", "rule_f1 ≥ 0.85", m["rule"]["micro"]["f1"] >= 0.85, m["rule"]["micro"]["f1"], 0.85, "target"),
    ]
    if b1 is not None:
        better = (
            (m["unsafe_approve"]["value"] or 0) < (b1["unsafe_approve"]["value"] or 0)
            and (m["false_reject"]["value"] or 0) < (b1["false_reject"]["value"] or 0)
        )
        checks.append(
            Check(
                "S5", "B1보다 unsafe_approve·false_reject 둘 다 낮다", better,
                {"unsafe_approve": m["unsafe_approve"]["value"], "false_reject": m["false_reject"]["value"]},
                {"unsafe_approve": b1["unsafe_approve"]["value"], "false_reject": b1["false_reject"]["value"]}, "target",
            )
        )
    return checks


def snapshot(m: dict[str, Any]) -> dict[str, float]:
    return {name: _get(m, path) for name, path, _ in TRACKED}


def regression(m: dict[str, Any], baseline: dict[str, float], label: str = "") -> list[Check]:
    checks: list[Check] = []
    for name, path, higher_better in TRACKED:
        cur, ref = _get(m, path), baseline.get(name)
        if ref is None or cur is None:
            continue
        ok = cur >= ref - TOLERANCE if higher_better else cur <= ref + TOLERANCE
        checks.append(Check(f"R:{label}{name}", f"{label}{name} 기준선 대비 악화 없음", ok, cur, ref, "regression"))
    return checks


def run_gate(
    datasets: dict[str, list[Example]], system: System, baseline: dict[str, dict[str, float]] | None,
) -> list[Check]:
    """datasets 에는 'dev', 'test', (있으면) 'redteam' 을 넣는다. 합격 기준(H1-H3)은 test 에만, 회귀 검사는 전부에 건다."""
    checks: list[Check] = []
    results = {name: metrics.compute(exs, [system(ex.creative) for ex in exs]) for name, exs in datasets.items()}
    if "test" in results:
        b1 = metrics.compute(datasets["test"], [naive_blocklist()(ex.creative) for ex in datasets["test"]])
        checks += acceptance(results["test"], b1)
    if "dev" in results:
        d = results["dev"]
        checks.append(Check("D1", "dev: hard_pass_through = 0", d["hard_pass_through"]["k"] == 0, d["hard_pass_through"]["k"], 0, "required"))
    if baseline:
        for name, m in results.items():
            if name in baseline:
                checks += regression(m, baseline[name], f"{name}:")
    return checks


def failed(checks: list[Check], kinds: tuple[str, ...] = ("required", "regression")) -> list[Check]:
    return [c for c in checks if c.kind in kinds and not c.ok]


def sensitivity(datasets: dict[str, list[Example]], baseline: dict[str, dict[str, float]] | None) -> dict[str, dict[str, Any]]:
    """구성요소를 하나씩 끄고 게이트를 돌린다. caught=True 면 게이트가 그 구성요소의 상실을 감지한다."""
    out: dict[str, dict[str, Any]] = {}
    for comp in ("normalize", "exceptions", "challenge", "landing"):
        cfg = ReviewConfig().without(comp)
        checks = run_gate(datasets, pipeline_system(cfg), baseline)
        bad = failed(checks)
        out[comp] = {"caught": bool(bad), "failed_checks": [c.id for c in bad]}
    return out
