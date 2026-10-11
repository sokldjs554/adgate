"""시스템을 데이터셋에 돌려 지표와 오류 목록을 만든다."""

from __future__ import annotations

from typing import Any

from ..policy import load_policy
from . import metrics
from .dataset import Example
from .systems import Prediction, System


def run_system(system: System, examples: list[Example]) -> list[Prediction]:
    return [system(ex.creative) for ex in examples]


def evaluate(system: System, examples: list[Example]) -> dict[str, Any]:
    return metrics.compute(examples, run_system(system, examples))


def errors(examples: list[Example], preds: list[Prediction]) -> list[dict[str, Any]]:
    """판정이 다르거나 규칙 집합이 다른 소재의 목록(디버깅·실패 기록용)."""
    policy = load_policy()
    out: list[dict[str, Any]] = []
    for ex, p in zip(examples, preds, strict=True):
        gset = sorted({v.rule_id for v in ex.gold.violations})
        pset = sorted({f.rule_id for f in p.findings if f.severity != "info" and f.rule_id in policy.rules})
        if p.outcome != ex.gold.decision or gset != pset:
            out.append(
                {
                    "id": ex.id,
                    "gold": ex.gold.decision, "pred": p.outcome,
                    "gold_rules": gset, "pred_rules": pset,
                    "headline": ex.creative.headline,
                    "tags": ex.gold.tags,
                    "findings": [
                        {"rule": f.rule_id, "code": f.code, "sev": f.severity, "field": f.field, "quote": f.quote, "src": f.source}
                        for f in p.findings if f.severity != "info"
                    ],
                }
            )
    return out
