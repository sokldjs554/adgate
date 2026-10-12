"""이미지 읽기 평가. 사양에서 나온 정답(글자 덩어리, 시각 단서)과 모델이 읽은 것을 견주고, 읽은 글자가 심사 판정을 얼마나 바꾸는지 잰다.

* **읽기**: 정답 글자 덩어리 각각이 읽은 글자에 있는가(재현율), 읽은 덩어리가 정답에 있는가(정밀도, 지어낸 글자 = 환각), 글자 오류율(CER), 시각 단서 F1.
  *어려운 글자*(작은 글씨·흐린 글씨)는 따로 센다.
* **판정 효과**: 이미지 속 글자를 (a) 전혀 안 주면, (b) 모델이 읽은 것을 주면, (c) 사양의 정답 글자를 주면 — 같은 결정론 심사의 정확도와 위험 승인율이 어떻게 달라지는가.
  읽기를 못 한 소재(호출 실패)는 승인하지 않는다(REVIEW, fail-closed).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from difflib import SequenceMatcher
from typing import Any

from ..config import ReviewConfig
from ..eval import metrics
from ..eval.metrics import ratio
from ..eval.systems import Prediction
from ..pipeline import review
from .dataset import VisionExample
from .read import ImageReading
from .spec import FLAGS, hard_texts, texts

_NOISE = re.compile(r"[\s\W_]+", re.UNICODE)
SIMILAR = 0.85


def norm(text: str) -> str:
    return _NOISE.sub("", text).lower()


def similar(a: str, b: str) -> bool:
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    return na == nb or SequenceMatcher(None, na, nb).ratio() >= SIMILAR


def cer(gold: str, got: str) -> float:
    """글자 오류율(편집 거리 / 정답 길이)."""
    a, b = norm(gold), norm(got)
    if not a:
        return 0.0 if not b else 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return round(prev[-1] / len(a), 4)


def score_reading(item: VisionExample, reading: ImageReading | None) -> dict[str, Any]:
    gold = texts(item.image)
    hard = set(hard_texts(item.image))
    got = [t.text for t in reading.texts if t.text.strip()] if reading else []
    hit = [any(similar(g, e) for e in got) for g in gold]
    hallucinated = [e for e in got if not any(similar(g, e) for g in gold)]
    gold_flags, got_flags = set(_flags(item)), set(reading.flags) if reading else set()
    return {
        "id": item.id, "read": reading is not None, "gold_n": len(gold), "hit": sum(hit), "hard_n": sum(g in hard for g in gold),
        "hard_hit": sum(h for g, h in zip(gold, hit, strict=True) if g in hard), "extracted_n": len(got), "hallucinated": hallucinated,
        "cer": cer(" ".join(gold), " ".join(got)) if reading else 1.0, "all_hit": all(hit) and not hallucinated,
        "flags_tp": sorted(gold_flags & got_flags), "flags_fp": sorted(got_flags - gold_flags), "flags_fn": sorted(gold_flags - got_flags),
        "missed": [g for g, h in zip(gold, hit, strict=True) if not h],
    }


def _flags(item: VisionExample) -> list[str]:
    from .spec import flags

    return flags(item.image)


def summarize_reading(scores: list[dict[str, Any]]) -> dict[str, Any]:
    n_gold = sum(s["gold_n"] for s in scores)
    n_hard = sum(s["hard_n"] for s in scores)
    n_extracted = sum(s["extracted_n"] for s in scores)
    tp = {f: sum(f in s["flags_tp"] for s in scores) for f in FLAGS}
    fp = {f: sum(f in s["flags_fp"] for s in scores) for f in FLAGS}
    fn = {f: sum(f in s["flags_fn"] for s in scores) for f in FLAGS}
    ttp, tfp, tfn = sum(tp.values()), sum(fp.values()), sum(fn.values())
    prec, rec = (ttp / (ttp + tfp) if ttp + tfp else 0.0), (ttp / (ttp + tfn) if ttp + tfn else 0.0)
    return {
        "items": len(scores), "read_failed": sum(not s["read"] for s in scores),
        "text_recall": ratio(sum(s["hit"] for s in scores), n_gold),
        "hard_text_recall": ratio(sum(s["hard_hit"] for s in scores), n_hard),
        "easy_text_recall": ratio(sum(s["hit"] - s["hard_hit"] for s in scores), n_gold - n_hard),
        "text_precision": ratio(n_extracted - sum(len(s["hallucinated"]) for s in scores), n_extracted),
        "hallucinated_items": sum(bool(s["hallucinated"]) for s in scores),
        "all_texts_exact": ratio(sum(s["all_hit"] for s in scores), len(scores)),
        "mean_cer": round(sum(s["cer"] for s in scores) / len(scores), 4) if scores else None,
        "flags": {"tp": ttp, "fp": tfp, "fn": tfn, "precision": round(prec, 4), "recall": round(rec, 4),
                  "f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 0.0, "by_flag": {f: {"tp": tp[f], "fp": fp[f], "fn": fn[f]} for f in FLAGS}},
        "missed_examples": [{"id": s["id"], "missed": s["missed"]} for s in scores if s["missed"]][:20],
        "hallucination_examples": [{"id": s["id"], "texts": s["hallucinated"]} for s in scores if s["hallucinated"]][:20],
    }


def decision_effect(items: list[VisionExample], readings: Mapping[str, ImageReading | None], cfg: ReviewConfig | None = None) -> dict[str, Any]:
    """이미지 속 글자를 (a) 안 줌 (b) 읽은 것 (c) 정답 글자로 주었을 때의 판정 지표."""
    cfg = cfg or ReviewConfig()
    out: dict[str, Any] = {}
    systems: dict[str, list[Prediction]] = {"no_image": [], "read": [], "oracle": []}
    examples = [it.oracle() for it in items]
    for it in items:
        for name in ("no_image", "oracle"):
            ex = it.example(None) if name == "no_image" else it.oracle()
            r = review(ex.creative, cfg)
            systems[name].append(Prediction(r.outcome, r.findings, r.stats.latency_ms, r.stats.quote_mismatch))
        reading = readings.get(it.id)
        if reading is None:  # 읽지 못했다 → 승인하지 않는다
            systems["read"].append(Prediction("REVIEW", [], 0.0, escalations=["image_unreadable"]))
        else:
            r = review(it.example(reading.image_text).creative, cfg)
            systems["read"].append(Prediction(r.outcome, r.findings, r.stats.latency_ms, r.stats.quote_mismatch))
    for name, preds in systems.items():
        out[name] = metrics.compute(examples, preds)
    only = [i for i, it in enumerate(items) if "image_only" in it.gold.tags]
    out["image_only"] = {
        name: metrics.compute([examples[i] for i in only], [preds[i] for i in only]) for name, preds in systems.items()
    } if only else {}
    return out
