"""섀도 운영과 승격 게이트. 후보 엔진을 같은 스트림에서 *결과에 영향 없이* 나란히 돌려 운영 엔진과 판정을 견주고, 승격해도 되는지 정한다.

1. **정답 없는 비교**(label-free): 판정이 달라진 소재의 비율과 방향(운영 APPROVE → 후보 REJECT/REVIEW 인가, 반대인가).
2. **사람 스팟체크**(정답 있음): 판정이 달라진 소재 중 최대 ``spot_n`` 건을 뽑아 사람이 맞는 쪽을 가린다(여기서는 정답 라벨, 선택적으로 검토자 오류율 ``reviewer_error``).
   후보가 운영보다 *틀린 건이 많으면* 막고, 같거나 적으면 통과시킨다.

게이트 규칙(측정 전에 정했다, docs/EVAL_OPS.md):
* 판정이 달라진 소재가 0건이면 통과(동일 후보).
* 달라진 비율이 ``max_disagreement`` 를 넘으면 스팟체크 없이 막는다(너무 많이 바뀌면 사람이 확인할 수 없다).
* 스팟체크에서 ``cand_wrong - prod_wrong > tolerance`` 이면 막는다. 아니면 통과.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from ..config import ReviewConfig
from ..eval.dataset import Example
from ..pipeline import review

Outcome = str
Engine = Callable[[Example], Outcome]


def engine_of(cfg: ReviewConfig) -> Engine:
    cache: dict[str, Outcome] = {}

    def run(e: Example) -> Outcome:
        if e.id not in cache:
            cache[e.id] = review(e.creative, cfg).outcome
        return cache[e.id]

    return run


@dataclass
class ShadowReport:
    n: int
    disagree: int
    transitions: dict[str, int]
    to_safer: int = 0          # 운영보다 엄격해졌다(APPROVE→REVIEW/REJECT, REVIEW→REJECT)
    to_looser: int = 0         # 운영보다 느슨해졌다
    ids: list[str] = field(default_factory=list)


_RANK = {"APPROVE": 0, "REVIEW": 1, "REJECT": 2}


def compare(stream: Sequence[Example], prod: Engine, cand: Engine) -> ShadowReport:
    trans: Counter[str] = Counter()
    safer = looser = 0
    ids: list[str] = []
    for e in stream:
        a, b = prod(e), cand(e)
        if a != b:
            trans[f"{a}→{b}"] += 1
            ids.append(e.id)
            if _RANK[b] > _RANK[a]:
                safer += 1
            else:
                looser += 1
    return ShadowReport(len(stream), len(ids), dict(sorted(trans.items())), safer, looser, ids)


@dataclass(frozen=True)
class GateRule:
    max_disagreement: float = 0.10
    spot_n: int = 40
    tolerance: int = 0
    reviewer_error: float = 0.0


@dataclass
class GateDecision:
    allow: bool
    reason: str
    report: ShadowReport
    spot: dict[str, int] = field(default_factory=dict)


def promote_gate(stream: Sequence[Example], prod: Engine, cand: Engine, rule: GateRule = GateRule(), *, seed: int = 0) -> GateDecision:  # noqa: B008
    rep = compare(stream, prod, cand)
    if rep.disagree == 0:
        return GateDecision(True, "동일", rep)
    if rep.disagree / rep.n > rule.max_disagreement:
        return GateDecision(False, f"판정이 {rep.disagree / rep.n:.1%} 달라졌다(>{rule.max_disagreement:.0%})", rep)
    rng = random.Random(seed)
    by_id = {e.id: e for e in stream}
    pick = rng.sample(rep.ids, min(rule.spot_n, len(rep.ids)))
    prod_wrong = cand_wrong = 0
    for i in pick:
        e = by_id[i]
        truth: str = e.gold.decision
        if rng.random() < rule.reviewer_error:                 # 검토자가 틀릴 수 있다: 정답이 아닌 판정을 정답으로 본다
            truth = rng.choice([o for o in _RANK if o != truth])
        prod_wrong += prod(e) != truth
        cand_wrong += cand(e) != truth
    spot = {"checked": len(pick), "prod_wrong": prod_wrong, "cand_wrong": cand_wrong}
    if cand_wrong - prod_wrong > rule.tolerance:
        return GateDecision(False, f"스팟체크에서 후보가 {cand_wrong - prod_wrong}건 더 틀렸다", rep, spot)
    return GateDecision(True, "스팟체크 통과", rep, spot)


# --- 평가 ---------------------------------------------------------------------------------------------------------------------------


def _truth_gap(stream: Sequence[Example], prod: Engine, cand: Engine) -> int:
    """정답 기준으로 후보가 운영보다 몇 건 더 틀렸는가(음수면 더 맞는다). 게이트 판단의 기준(oracle)이다."""
    return sum(cand(e) != e.gold.decision for e in stream) - sum(prod(e) != e.gold.decision for e in stream)


def candidates() -> dict[str, ReviewConfig]:
    """운영 엔진(기본 설정) 대비 후보 구성: 구성요소를 하나씩 끈 것(열화) 과 운영을 열화시킨 뒤 기본 설정으로 되돌린 것(개선) 쌍은 evaluate_shadow 가 만든다."""
    base = ReviewConfig()
    return {
        "same": base,
        "no_normalize": base.without("normalize"),
        "no_exceptions": base.without("exceptions"),
        "no_challenge": base.without("challenge"),
        "no_landing": base.without("landing"),
    }


def evaluate_shadow(pool: Sequence[Example], *, reps: int = 20, sample: int = 150, rule: GateRule = GateRule()) -> dict[str, object]:  # noqa: B008
    """후보가 (a) 같다 (b) 열화됐다 (c) 개선됐다 일 때 게이트가 옳게 막고 통과시키는가. 같은 풀에서 ``sample`` 건을 ``reps`` 번 뽑아 스트림으로 쓴다."""
    prod_cfg = ReviewConfig()
    engines = {k: engine_of(c) for k, c in candidates().items()}
    prod = engine_of(prod_cfg)
    rows: list[dict[str, object]] = []
    cases: list[tuple[str, str, Engine, Engine]] = []
    for k, eng in engines.items():
        cases.append((k, "same" if k == "same" else "degraded", prod, eng))
    for k in ("no_normalize", "no_exceptions", "no_challenge", "no_landing"):   # 운영이 열화된 상태에서 기본 설정 후보가 들어오는 경우 = 개선
        cases.append((f"fix_{k}", "improved", engines[k], prod))
    for name, kind, p, c in cases:
        allowed = blocked = right = 0
        flips = []
        for r in range(reps):
            rng = random.Random(500 + r)
            stream = rng.sample(list(pool), min(sample, len(pool)))
            d = promote_gate(stream, p, c, rule, seed=r)
            allowed += d.allow
            blocked += not d.allow
            gap = _truth_gap(stream, p, c)
            should_allow = gap <= rule.tolerance
            right += d.allow == should_allow
            flips.append(d.report.disagree)
        rows.append({
            "candidate": name, "kind": kind, "streams": reps, "allowed": allowed, "blocked": blocked, "right": right,
            "disagree_mean": round(sum(flips) / len(flips), 2),
        })
    return {"sample": sample, "reps": reps, "rule": {"max_disagreement": rule.max_disagreement, "spot_n": rule.spot_n, "tolerance": rule.tolerance}, "rows": rows}
