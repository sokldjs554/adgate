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


# --------------------------------------------------------------------------- 라벨러가 여럿일 때
def load_multi(path: str | Path) -> dict[str, dict[str, dict[str, Any]]]:
    """``labeler`` 필드가 있는 JSON Lines → {라벨러: {id: 행}}."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out.setdefault(r["labeler"], {})[r["id"]] = r
    return out


def fleiss_kappa(rows: list[list[str]]) -> float:
    """Fleiss' κ. ``rows`` 는 대상마다 평가자들의 범주(평가자 수가 모두 같아야 한다)."""
    n_items, n_raters = len(rows), len(rows[0])
    cats = sorted({c for r in rows for c in r})
    counts = [[r.count(c) for c in cats] for r in rows]
    p_i = [(sum(x * x for x in row) - n_raters) / (n_raters * (n_raters - 1)) for row in counts]
    p_bar = sum(p_i) / n_items
    p_j = [sum(row[j] for row in counts) / (n_items * n_raters) for j in range(len(cats))]
    p_e = sum(x * x for x in p_j)
    return (p_bar - p_e) / (1 - p_e) if p_e < 1 else 1.0


def _cohen(a: list[str], b: list[str]) -> float:
    n = len(a)
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in CATS)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def compare_multi(
    examples: list[Example], by_labeler: dict[str, dict[str, dict[str, Any]]], *, alt: dict[str, str] | None = None, dispute_at: int = 2,
) -> dict[str, Any]:
    """라벨러 여럿과 작성자 정답의 일치도.

    * 라벨러마다 ``compare`` (판정 일치·κ·규칙 F1).
    * 다중 일치: 라벨러끼리의 Fleiss κ, 작성자를 평가자로 넣은 Fleiss κ, 쌍별 Cohen κ.
    * ``disputed``: 라벨러 ``dispute_at`` 명 이상의 판정이 작성자 정답과 다르면 *기계적으로* 붙는다(판단하지 않는다).
    * ``alt`` 가 있으면(작성자가 적은 대안 판정) 라벨러의 판정이 정답 *또는 대안*이면 맞다고 보는 관대한 일치도를 따로 낸다.
    * 슬라이스(작성자 태그 clear/normal/gray)별 일치.
    """
    names = sorted(by_labeler)
    for n in names:
        missing = [ex.id for ex in examples if ex.id not in by_labeler[n]]
        if missing:
            raise ValueError(f"라벨러 {n} 누락: {missing[:5]}")
    per = {n: compare(examples, by_labeler[n]) for n in names}
    ids = [ex.id for ex in examples]
    dec = {n: [by_labeler[n][i]["decision"] for i in ids] for n in names}
    gold = [ex.gold.decision for ex in examples]
    disputed = []
    for k, ex in enumerate(examples):
        wrong = sum(dec[n][k] != gold[k] for n in names)
        if wrong >= dispute_at:
            disputed.append(ex.id)
    all_agree = sum(len({dec[n][k] for n in names} | {gold[k]}) == 1 for k in range(len(ids)))
    slices: dict[str, dict[str, Any]] = {}
    for tag in ("clear", "normal", "gray"):
        sel = [k for k, ex in enumerate(examples) if tag in ex.gold.tags]
        if sel:
            slices[tag] = {
                "n": len(sel),
                "mean_agreement_with_author": round(sum(dec[n][k] == gold[k] for n in names for k in sel) / (len(sel) * len(names)), 4),
                "all_agree": sum(len({dec[n][k] for n in names} | {gold[k]}) == 1 for k in sel),
                "disputed": sum(examples[k].id in disputed for k in sel),
            }
    out: dict[str, Any] = {
        "n": len(ids), "labelers": names,
        "per_labeler": {n: {"agreement": per[n]["decision_agreement"], "kappa": per[n]["kappa"], "rule_f1": per[n]["rule"]["f1"]} for n in names},
        "mean_agreement_with_author": round(sum(per[n]["decision_agreement"]["value"] for n in names) / len(names), 4),
        "fleiss_kappa_labelers": round(fleiss_kappa([[dec[n][k] for n in names] for k in range(len(ids))]), 4),
        "fleiss_kappa_with_author": round(fleiss_kappa([[*(dec[n][k] for n in names), gold[k]] for k in range(len(ids))]), 4),
        "pairwise_kappa": {f"{a}-{b}": round(_cohen(dec[a], dec[b]), 4) for i, a in enumerate(names) for b in names[i + 1:]},
        "all_agree_with_author": {"k": all_agree, "n": len(ids)},
        "disputed": {"rule": f"라벨러 {dispute_at}명 이상이 작성자 정답과 다름", "n": len(disputed), "ids": disputed},
        "slices": slices,
    }
    if alt:
        ok = sum((dec[n][k] == gold[k]) or (dec[n][k] == alt.get(ids[k])) for n in names for k in range(len(ids)))
        out["lenient_agreement_with_author_or_alt"] = {"k": ok, "n": len(ids) * len(names), "value": round(ok / (len(ids) * len(names)), 4)}
    return out
