"""독립 라벨러와 설계자 정답의 일치도(판정 Cohen's κ, 규칙 단위 P/R/F1)."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .dataset import Example

CATS = ("APPROVE", "REVIEW", "REJECT")


def load_labels(path: str | Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["id"]] = r
    return out


def compare(examples: list[Example], labels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ids = [ex.id for ex in examples]
    missing = [i for i in ids if i not in labels]
    if missing:
        raise ValueError(f"라벨 누락: {missing[:5]}")
    n = len(examples)
    conf: Counter[tuple[str, str]] = Counter()
    tp = fp = fn = 0
    per_rule: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    disagreements: list[dict[str, Any]] = []
    quoted = overlap = exact = 0
    for ex in examples:
        lab = labels[ex.id]
        conf[(ex.gold.decision, lab["decision"])] += 1
        for v in ex.gold.violations:
            if not (v.field and v.quote):
                continue
            quoted += 1
            text = ex.creative.text(v.field)
            gi = text.find(v.quote)
            same = [x for x in lab["violations"] if x["rule_id"] == v.rule_id and x.get("field") == v.field and x.get("quote")]
            exact += any(x["quote"] == v.quote for x in same)
            for x in same:
                xi = text.find(x["quote"])
                if gi >= 0 and xi >= 0 and xi < gi + len(v.quote) and gi < xi + len(x["quote"]):
                    overlap += 1
                    break
        g = {v.rule_id for v in ex.gold.violations}
        p = {v["rule_id"] for v in lab["violations"]}
        for r in g & p:
            per_rule[r][0] += 1
        for r in p - g:
            per_rule[r][1] += 1
        for r in g - p:
            per_rule[r][2] += 1
        tp, fp, fn = tp + len(g & p), fp + len(p - g), fn + len(g - p)
        if ex.gold.decision != lab["decision"] or g != p:
            disagreements.append(
                {
                    "id": ex.id, "gold": ex.gold.decision, "labeler": lab["decision"],
                    "gold_rules": sorted(g), "labeler_rules": sorted(p),
                    "confidence": lab.get("confidence"), "note": lab.get("note") or "",
                    "headline": ex.creative.headline,
                }
            )
    po = sum(conf[(c, c)] for c in CATS) / n
    pe = sum(
        (sum(conf[(c, d)] for d in CATS) / n) * (sum(conf[(d, c)] for d in CATS) / n) for c in CATS
    )
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return {
        "n": n,
        "decision_agreement": {"k": sum(conf[(c, c)] for c in CATS), "n": n, "value": round(po, 4)},
        "kappa": round(kappa, 4),
        "confusion": {g: {p: conf[(g, p)] for p in CATS} for g in CATS},
        "rule": {
            "precision": round(prec, 4), "recall": round(rec, 4),
            "f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 0.0, "tp": tp, "fp": fp, "fn": fn,
            "per_rule": {r: {"agree": a, "labeler_extra": b, "labeler_missing": c} for r, (a, b, c) in sorted(per_rule.items())},
        },
        "quote": {"gold_quoted": quoted, "overlap": overlap, "exact": exact},
        "disagreements": disagreements,
    }
