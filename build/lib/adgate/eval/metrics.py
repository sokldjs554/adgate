"""지표 계산. docs/EVAL_PLAN.md 3절의 정의를 그대로 구현한다."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from ..policy import load_policy
from .dataset import Example
from .systems import Prediction

OUTCOMES = ("APPROVE", "REVIEW", "REJECT")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def ratio(k: int, n: int) -> dict[str, Any]:
    return {"k": k, "n": n, "value": round(k / n, 4) if n else None, "ci": list(wilson(k, n))}


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))
    return round(s[idx], 3)


def _gold_spans(ex: Example) -> dict[tuple[str, str], list[tuple[int, int]]]:
    spans: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    for v in ex.gold.violations:
        if v.field and v.quote:
            i = ex.creative.text(v.field).find(v.quote)
            if i >= 0:
                spans[(v.rule_id, v.field)].append((i, i + len(v.quote)))
    return spans


def compute(examples: list[Example], preds: list[Prediction]) -> dict[str, Any]:
    assert len(examples) == len(preds)
    policy = load_policy()
    n = len(examples)
    gold: list[str] = [ex.gold.decision for ex in examples]
    pred: list[str] = [p.outcome for p in preds]
    conf: Counter[tuple[str, str]] = Counter(zip(gold, pred, strict=True))
    n_r, n_v, n_a = gold.count("REJECT"), gold.count("REVIEW"), gold.count("APPROVE")

    def cnt(g: str | tuple[str, ...], p: str | tuple[str, ...]) -> int:
        gs = (g,) if isinstance(g, str) else g
        ps = (p,) if isinstance(p, str) else p
        return sum(v for (a, b), v in conf.items() if a in gs and b in ps)

    auto = cnt(OUTCOMES, ("APPROVE", "REJECT"))
    auto_ok = sum(conf[(c, c)] for c in ("APPROVE", "REJECT"))

    f1s = []
    for c in OUTCOMES:
        tp = conf[(c, c)]
        fp = sum(conf[(g, c)] for g in OUTCOMES if g != c)
        fn = sum(conf[(c, p)] for p in OUTCOMES if p != c)
        f1s.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))

    # 규칙 단위 (소재, 규칙) 쌍
    tp = fp = fn = 0
    per_rule: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    loc_k = loc_n = fin_k = fin_n = 0
    ground_k = ground_n = 0
    for ex, p in zip(examples, preds, strict=True):
        gset = {v.rule_id for v in ex.gold.violations}
        pfind = [f for f in p.findings if f.severity != "info" and f.rule_id in policy.rules]
        pset = {f.rule_id for f in pfind}
        for r in gset & pset:
            per_rule[r][0] += 1
        for r in pset - gset:
            per_rule[r][1] += 1
        for r in gset - pset:
            per_rule[r][2] += 1
        tp, fp, fn = tp + len(gset & pset), fp + len(pset - gset), fn + len(gset - pset)

        gold_spans = _gold_spans(ex)
        for r in gset & pset:
            have_quote = [k for k in gold_spans if k[0] == r]
            if not have_quote:
                continue
            loc_n += 1
            hit = any(
                f.rule_id == r and f.start is not None and f.end is not None
                and any(
                    f.field == fld and f.start < ge and gs < f.end
                    for (rr, fld), spans in gold_spans.items() if rr == r for gs, ge in spans
                )
                for f in pfind
            )
            loc_k += hit
        gfin = [v for v in ex.gold.violations if v.rule_id == "AD-FIN-01"]
        pfin = [f for f in pfind if f.rule_id == "AD-FIN-01"]
        if gfin and pfin:
            fin_n += 1
            fin_k += set(gfin[0].missing or []) == set(pfin[0].missing or [])
        for f in p.findings:
            if f.quote is not None and f.field is not None and f.start is not None and f.end is not None:
                ground_n += 1
                ground_k += ex.creative.text(f.field)[f.start:f.end] == f.quote

    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0

    # 슬라이스: 태그별
    by_tag: dict[str, list[int]] = defaultdict(list)
    for i, ex in enumerate(examples):
        for t in ex.gold.tags:
            by_tag[t].append(i)
    slices: dict[str, Any] = {}
    for tag, idxs in sorted(by_tag.items()):
        if len(idxs) < 3:
            continue
        g = [gold[i] for i in idxs]
        q = [pred[i] for i in idxs]
        slices[tag] = {
            "n": len(idxs),
            "accuracy": ratio(sum(a == b for a, b in zip(g, q, strict=True)), len(idxs)),
            "unsafe_approve": ratio(
                sum(a in ("REJECT", "REVIEW") and b == "APPROVE" for a, b in zip(g, q, strict=True)),
                sum(a in ("REJECT", "REVIEW") for a in g),
            ),
            "false_reject": ratio(
                sum(a == "APPROVE" and b == "REJECT" for a, b in zip(g, q, strict=True)), sum(a == "APPROVE" for a in g)
            ),
        }

    lat = [p.latency_ms for p in preds]
    return {
        "n": n,
        "gold_counts": {c: gold.count(c) for c in OUTCOMES},
        "pred_counts": {c: pred.count(c) for c in OUTCOMES},
        "confusion": {g: {p: conf[(g, p)] for p in OUTCOMES} for g in OUTCOMES},
        "accuracy": ratio(sum(conf[(c, c)] for c in OUTCOMES), n),
        "macro_f1": round(sum(f1s) / 3, 4),
        "hard_pass_through": ratio(cnt("REJECT", "APPROVE"), n_r),
        "unsafe_approve": ratio(cnt(("REJECT", "REVIEW"), "APPROVE"), n_r + n_v),
        "false_reject": ratio(cnt("APPROVE", "REJECT"), n_a),
        "over_review": ratio(cnt("APPROVE", "REVIEW"), n_a),
        "downgrade": ratio(cnt("REJECT", "REVIEW"), n_r),
        "auto_rate": ratio(auto, n),
        "auto_precision": ratio(auto_ok, auto),
        "rule": {
            "micro": {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4), "tp": tp, "fp": fp, "fn": fn},
            "per_rule": {r: {"tp": a, "fp": b, "fn": c} for r, (a, b, c) in sorted(per_rule.items())},
        },
        "localization": ratio(loc_k, loc_n),
        "fin_missing_exact": ratio(fin_k, fin_n),
        "grounding": ratio(ground_k, ground_n),
        "latency_ms": {"p50": percentile(lat, 0.5), "p95": percentile(lat, 0.95)},
        "slices": slices,
    }
