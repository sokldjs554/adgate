"""환류: 운영에서 사람 검토자가 내린 결정을 엔진 개선의 입력으로 되돌린다.

``Store`` 에서 사람이 결정한 실행(``decided_by='human'``)을 읽어, 엔진이 보류(REVIEW)로 보낸 건에 사람이 어떻게 답했는지 모은다.

* **재정의율(override rate)**: 규칙마다 '그 규칙이 발견된 보류 건 중 사람이 승인한 비율'. 높으면 그 규칙의 예외(정상 유사 처리)를 손볼 후보다.
* **회귀 후보**: 사람이 엔진의 발견과 반대로 결정한 건(발견이 있는데 승인 / 발견이 없는데 반려)을 소재 + 사람 판정 + 사유 + 출처로 내보낸다.
  자동으로 동결 셋에 들어가지 않는다 — 라벨링 큐(``adjudicate``)에서 *독립 라벨러 둘*을 거쳐야 정답이 된다. 검토자 한 명의 결정은 운영의 판단이지 정답이 아니다.
* 개인정보: 검토자 이름과 사유는 내보내지 않는다(검토자 신원은 ``reviewer_id`` 해시 앞 8자).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import Creative
from ..store import Run, Store


@dataclass(frozen=True)
class FeedbackItem:
    run_id: str
    kind: str                  # override_approve | override_reject | confirmed
    engine: str                # 엔진의 판정(보류)
    human: str                 # 사람의 최종 판정
    rules: tuple[str, ...]     # 엔진이 낸 발견의 규칙(info 제외)
    category: str
    creative: dict[str, Any]
    reviewer: str              # 해시 앞 8자


def _hash(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]


def classify(run: Run, reviewer: str) -> FeedbackItem | None:
    if run.decided_by != "human":
        return None
    findings = [f for f in run.result["findings"] if f["severity"] != "info"]
    rules = tuple(sorted({f["rule_id"] for f in findings}))
    human, engine = run.final_outcome, run.result["outcome"]
    if human == "APPROVE" and rules:
        kind = "override_approve"          # 엔진은 의심했는데 사람이 통과시켰다 → 오탐 후보
    elif human == "REJECT" and not rules:
        kind = "override_reject"           # 엔진이 근거를 못 댔는데 사람이 반려했다 → 미탐 후보
    else:
        kind = "confirmed"
    return FeedbackItem(run.id, kind, engine, human, rules, run.creative["advertiser"]["category"], run.creative, _hash(reviewer))


def mine(store: Store, *, page: int = 200) -> list[FeedbackItem]:
    out: list[FeedbackItem] = []
    offset = 0
    while True:
        runs = store.list_runs(state="completed", limit=page, offset=offset, oldest_first=True)
        if not runs:
            return out
        for r in runs:
            if r.decided_by != "human":
                continue
            who = next((e["payload"].get("reviewer", "") for e in store.events(r.id) if e["type"] == "resumed"), "")
            item = classify(r, who)
            if item:
                out.append(item)
        offset += len(runs)


def override_stats(items: list[FeedbackItem]) -> dict[str, Any]:
    n = len(items)
    per_rule: dict[str, Counter[str]] = defaultdict(Counter)
    per_cat: dict[str, Counter[str]] = defaultdict(Counter)
    for it in items:
        for r in it.rules:
            per_rule[r][it.human] += 1
        per_cat[it.category][it.kind] += 1
    return {
        "human_decided": n, "by_kind": dict(Counter(i.kind for i in items)),
        "override_approve_rate": round(sum(i.kind == "override_approve" for i in items) / n, 4) if n else None,
        "rules": {r: {"seen": sum(c.values()), "human_approved": c["APPROVE"], "override_rate": round(c["APPROVE"] / sum(c.values()), 4)} for r, c in sorted(per_rule.items())},
        "categories": {c: dict(k) for c, k in sorted(per_cat.items())},
    }


def export_candidates(items: list[FeedbackItem], path: Path) -> int:
    """오탐·미탐 후보를 라벨링 큐 입력으로 내보낸다(사람 판정은 '운영 검토자의 의견'으로만 적는다)."""
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for it in items:
            if it.kind == "confirmed":
                continue
            Creative.model_validate(it.creative)        # 형식이 어긋난 소재는 내보내지 않는다
            fh.write(json.dumps({
                "run_id": it.run_id, "source": "production_review", "kind": it.kind, "engine_outcome": it.engine, "reviewer_opinion": it.human,
                "engine_rules": list(it.rules), "reviewer_id": it.reviewer, "creative": it.creative,
            }, ensure_ascii=False) + "\n")
            n += 1
    return n
