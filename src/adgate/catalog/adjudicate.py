"""라벨 수집과 정답 승격: 소재마다 서로 다른 라벨러 둘이 *정답과 엔진 판정을 가린 채* 라벨을 붙이고, 갈리면 세 번째가 중재한다.

* ``assign``: 라벨러 부하를 고르게 나누고 한 소재를 같은 라벨러가 두 번 보지 않게 한다(시드 고정).
* ``blind_view``: 라벨러에게 보여 줄 것은 광고 소재 글뿐이다. 엔진 판정·우선순위·출처는 앵커링을 일으키므로 보이지 않는다.
* ``resolve``: 둘이 판정이 같으면 정답. 다르면 세 번째 라벨을 요구한다. 셋이 모두 다르면 ``disputed`` — 정책 해석이 필요하다(``needs_policy_ruling``)는 뜻이지 정답이 아니다.
  규칙(위반)은 라벨 둘 이상이 든 규칙만 정답에 넣는다. 인용은 그 규칙을 든 첫 라벨의 것이다.
* ``promote``: 정답이 정해진 소재를 ``data/candidates/*.jsonl`` 에 *출처와 함께* 쓴다. 동결 셋(``MANIFEST.sha256``)에는 절대 쓰지 않는다.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..eval.agreement import _cohen
from ..models import AD_FIELDS, Creative

DECISIONS = ("APPROVE", "REVIEW", "REJECT")


@dataclass(frozen=True)
class Label:
    labeler: str
    decision: str
    rules: tuple[str, ...] = ()
    quotes: tuple[tuple[str, str, str], ...] = ()      # (rule_id, field, quote)
    note: str = ""


@dataclass
class LabelTask:
    id: str
    creative: Creative
    source: str                                         # 어디서 왔나(우선순위 큐 · 운영 환류 · 무작위)
    priority: float = 0.0
    labels: list[Label] = field(default_factory=list)


def blind_view(task: LabelTask) -> dict[str, Any]:
    """라벨러에게 보이는 것. 엔진 판정·우선순위·출처는 없다."""
    c = task.creative
    return {"task_id": task.id, "category": c.advertiser.category, "advertiser_verified": c.advertiser.verified, **{f: c.text(f) or None for f in (*AD_FIELDS, "landing_text")}}


def assign(task_ids: Sequence[str], labelers: Sequence[str], *, k: int = 2, seed: int = 0) -> dict[str, list[str]]:
    if len(set(labelers)) < k:
        raise ValueError(f"라벨러가 {k}명 이상 필요하다")
    rng = random.Random(seed)
    load: Counter[str] = Counter({name: 0 for name in labelers})
    out: dict[str, list[str]] = {}
    for tid in task_ids:
        order = sorted(labelers, key=lambda n: (load[n], rng.random()))
        out[tid] = order[:k]
        for n in out[tid]:
            load[n] += 1
    return out


Status = Literal["agreed", "needs_third", "resolved_majority", "disputed"]


@dataclass(frozen=True)
class Resolution:
    status: Status
    decision: str | None
    rules: tuple[str, ...] = ()
    quotes: tuple[tuple[str, str, str], ...] = ()
    needs_policy_ruling: bool = False


def resolve(labels: Sequence[Label]) -> Resolution:
    if len(labels) < 2:
        return Resolution("needs_third", None)
    if len({lab.labeler for lab in labels}) != len(labels):
        raise ValueError("같은 라벨러가 한 소재에 두 번 라벨을 붙일 수 없다")
    first, second = labels[0], labels[1]
    if first.decision == second.decision:
        status: Status = "agreed"
        decision: str | None = first.decision
        used: Sequence[Label] = labels[:2]
    elif len(labels) == 2:
        return Resolution("needs_third", None)
    else:
        top, n = Counter(lab.decision for lab in labels[:3]).most_common(1)[0]
        if n < 2:
            return Resolution("disputed", None, needs_policy_ruling=True)
        status, decision, used = "resolved_majority", top, labels[:3]
    # 규칙: 판정이 정답과 같은 쪽 라벨 중 둘 이상이 든 규칙
    agree_on = [lab for lab in used if lab.decision == decision]
    counts: Counter[str] = Counter(r for lab in agree_on for r in set(lab.rules))
    rules = tuple(sorted(r for r, n in counts.items() if n >= 2))
    quotes: list[tuple[str, str, str]] = []
    for r in rules:
        q = next((q for lab in agree_on for q in lab.quotes if q[0] == r), None)
        if q:
            quotes.append(q)
    if decision == "APPROVE":
        rules, quotes = (), []
    return Resolution(status, decision, rules, tuple(quotes))


def agreement_stats(tasks: Sequence[LabelTask]) -> dict[str, Any]:
    """첫 두 라벨의 원시 일치율과 Cohen's κ, 상태별 소재 수."""
    pairs = [(t.labels[0].decision, t.labels[1].decision) for t in tasks if len(t.labels) >= 2]
    statuses = Counter(resolve(t.labels).status for t in tasks)
    out: dict[str, Any] = {"tasks": len(tasks), "double_labeled": len(pairs), "status": dict(statuses)}
    if pairs:
        a, b = [p[0] for p in pairs], [p[1] for p in pairs]
        out["raw_agreement"] = round(sum(x == y for x, y in pairs) / len(pairs), 4)
        out["kappa"] = round(_cohen(a, b), 4)
    return out


def promote(tasks: Sequence[LabelTask], path: Path, *, batch: str, frozen: set[str] | None = None) -> dict[str, int]:
    """정답이 정해진 소재만 후보 파일에 쓴다. ``frozen`` 파일 이름(동결 셋)에는 쓰지 않는다. 같은 id 는 덮어쓰지 않는다."""
    if frozen and path.name in frozen:
        raise PermissionError(f"{path.name} 은(는) 동결 셋이다 — 후보 라벨은 data/candidates/ 에 쓴다")
    existing = {json.loads(ln)["id"] for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()} if path.exists() else set()
    written = skipped = unresolved = 0
    with path.open("a", encoding="utf-8") as fh:
        for t in tasks:
            res = resolve(t.labels)
            if res.decision is None:
                unresolved += 1
                continue
            if t.id in existing:
                skipped += 1
                continue
            violations: list[dict[str, str | None]] = (
                [{"rule_id": r, "field": f, "quote": q} for r, f, q in res.quotes] or [{"rule_id": r, "field": None, "quote": None} for r in res.rules]
            )
            rec: dict[str, Any] = {
                "id": t.id, "creative": json.loads(t.creative.model_dump_json()),
                "gold": {"decision": res.decision, "violations": violations, "tags": ["candidate", res.status]},
                "provenance": {"batch": batch, "source": t.source, "labelers": [lab.labeler for lab in t.labels], "status": res.status},
            }
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1
    return {"written": written, "skipped_existing": skipped, "unresolved": unresolved}


# --- 시뮬레이션(분석 코드 점검) ---------------------------------------------------------------------------------------------------------


def simulate(n: int, accuracy: float, hard_share: float = 0.2, hard_accuracy: float = 0.55, seed: int = 0) -> dict[str, Any]:
    """라벨러 정확도 ``accuracy``(어려운 소재는 ``hard_accuracy``)일 때 이중 라벨 + 중재가 정답을 얼마나 되찾는가."""
    rng = random.Random(seed)
    status: Counter[str] = Counter()
    right = 0
    resolved = 0
    for _ in range(n):
        gold = rng.choice(DECISIONS)
        acc = hard_accuracy if rng.random() < hard_share else accuracy

        def label(name: str, gold: str = gold, acc: float = acc) -> Label:
            d = gold if rng.random() < acc else rng.choice([x for x in DECISIONS if x != gold])
            return Label(name, d)

        labels = [label("a"), label("b")]
        res = resolve(labels)
        if res.status == "needs_third":
            labels.append(label("c"))
            res = resolve(labels)
        status[res.status] += 1
        if res.decision is not None:
            resolved += 1
            right += res.decision == gold
    return {"n": n, "status": dict(status), "resolved": resolved, "gold_accuracy": round(right / resolved, 4) if resolved else None, "disputed_share": round(status["disputed"] / n, 4)}
