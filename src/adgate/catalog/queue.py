"""라벨링 우선순위 큐. 정답 없이(label-free) 엔진 출력과 입력만으로 '사람이 먼저 봐야 할 소재'에 점수를 매기고, 정답이 있는 풀에서 *엔진 오류를 얼마나 빨리 찾는가*로 잰다.

신호(가중치는 측정 전에 정했다, docs/EVAL_LABELING.md):
* ``review`` (3) — 엔진이 REVIEW 로 보류했다(엔진 스스로 모른다고 한 것).
* ``challenge_only`` (2) — 발견이 모두 느슨한 두 번째 검사(``source='challenge'``)에서만 나왔다.
* ``escalation`` (1) — 에스컬레이션(judge 불가·근거 없는 의심 …)이 붙었다.
* ``cues`` (1×개수, 최대 3) — 위험 개념 6종(보장·최상급·긴급·효능·비교·추천) 어휘가 소재 글에 있다. 엔진이 예외로 통과시킨 소재의 *남은 의심*을 줍는다.
* ``prior`` (0.5) — 대출·투자·건강기능식품 업종(규칙이 가장 많이 걸리는 업종).
* ``judge_disagree`` (2) — 녹음된 judge 가 있고 그 발견이 엔진의 판정을 바꾼다(judge 녹음이 있는 소재에서만).
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from ..config import ReviewConfig
from ..eval.dataset import Example
from ..judge_recorded import RecordedJudge
from ..normalize import build
from ..pipeline import review

CUES: dict[str, re.Pattern[str]] = {
    "guarantee": re.compile(r"보장|확정|무조건|반드시|틀림없|손해|손실없|원금"),
    "superlative": re.compile(r"최고|최초|1위|유일|완벽|no1|제일|가장"),
    "urgency": re.compile(r"오늘만|마감|한정|선착순|서두|놓치|기회|곧종료"),
    "health": re.compile(r"치료|완치|효능|효과|개선|예방|질환|통증|염증|혈당|혈압"),
    "comparison": re.compile(r"보다|대비|절반|경쟁|타사|다른곳|옆가게"),
    "endorsement": re.compile(r"추천|후기|만족|전문가|의사|임상|인증"),
}
PRIOR_CATEGORIES = frozenset({"loan", "invest", "supplement"})
WEIGHTS = {"review": 3.0, "challenge_only": 2.0, "escalation": 1.0, "cue": 1.0, "prior": 0.5, "judge_disagree": 2.0}
VARIANTS = ("engine", "engine+cues", "engine+cues+judge")


def cue_count(e: Example) -> int:
    c = e.creative
    text = build(" ".join(x for x in (c.headline, c.body, c.cta, c.disclosure or "", c.image_text or "") if x)).tight
    return sum(bool(p.search(text)) for p in CUES.values())


@dataclass
class Signals:
    review: bool = False
    challenge_only: bool = False
    escalation: bool = False
    cues: int = 0
    prior: bool = False
    judge_disagree: bool | None = None       # None: 녹음된 judge 가 없다
    outcome: str = "APPROVE"


def signals(e: Example, cfg: ReviewConfig | None = None, judge: RecordedJudge | None = None) -> Signals:
    cfg = cfg or ReviewConfig()
    r = review(e.creative, cfg)
    real = [f for f in r.findings if f.severity != "info"]
    s = Signals(
        review=r.outcome == "REVIEW", challenge_only=bool(real) and all(f.source == "challenge" for f in real), escalation=bool(r.escalations),
        cues=min(cue_count(e), 3), prior=e.creative.advertiser.category in PRIOR_CATEGORIES, outcome=r.outcome,
    )
    if judge is not None and judge.has(e.creative):
        j = review(e.creative, ReviewConfig(judge=judge, judge_cap_review=True))
        s.judge_disagree = j.outcome != r.outcome
    return s


def score(s: Signals, variant: str) -> float:
    pts = WEIGHTS["review"] * s.review + WEIGHTS["challenge_only"] * s.challenge_only + WEIGHTS["escalation"] * s.escalation
    if variant in ("engine+cues", "engine+cues+judge"):
        pts += WEIGHTS["cue"] * s.cues + WEIGHTS["prior"] * s.prior
    if variant == "engine+cues+judge" and s.judge_disagree:
        pts += WEIGHTS["judge_disagree"]
    return pts


@dataclass
class Ranked:
    id: str
    score: float
    outcome: str
    gold: str
    error: bool
    unsafe_approve: bool
    reasons: list[str] = field(default_factory=list)


def reasons(s: Signals) -> list[str]:
    out = []
    if s.review:
        out.append("엔진이 보류(REVIEW)")
    if s.challenge_only:
        out.append("느슨한 검사에서만 의심")
    if s.escalation:
        out.append("에스컬레이션")
    if s.cues:
        out.append(f"위험 개념 어휘 {s.cues}종")
    if s.judge_disagree:
        out.append("judge 와 판정이 갈림")
    return out


def rank(items: Sequence[Example], variant: str, judge: RecordedJudge | None = None, *, cfg: ReviewConfig | None = None) -> list[Ranked]:
    rows = []
    for e in items:
        s = signals(e, cfg, judge)
        rows.append(Ranked(e.id, score(s, variant), s.outcome, e.gold.decision, s.outcome != e.gold.decision, e.gold.decision != "APPROVE" and s.outcome == "APPROVE", reasons(s)))
    return sorted(rows, key=lambda r: (-r.score, r.id))


def recall_at(ranked: Sequence[Ranked], frac: float, key: Callable[[Ranked], bool]) -> tuple[int, int]:
    """상위 ``frac`` 에 든 *정답 오류*의 수 / 전체 오류의 수."""
    k = max(1, round(frac * len(ranked)))
    total = sum(key(r) for r in ranked)
    return sum(key(r) for r in ranked[:k]), total


def auc(ranked: Sequence[Ranked], key: Callable[[Ranked], bool]) -> float | None:
    """오류 소재가 정상 소재보다 앞에 놓일 확률(동점은 0.5)."""
    pos = [r.score for r in ranked if key(r)]
    neg = [r.score for r in ranked if not key(r)]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 4)


def random_recall(ranked: Sequence[Ranked], frac: float, key: Callable[[Ranked], bool], *, draws: int = 500, seed: int = 0) -> float:
    rng = random.Random(seed)
    k = max(1, round(frac * len(ranked)))
    total = sum(key(r) for r in ranked)
    if not total:
        return 0.0
    hit = sum(sum(key(r) for r in rng.sample(list(ranked), k)) for _ in range(draws)) / draws
    return round(hit / total, 4)


def evaluate_queue(sets: dict[str, list[Example]], judge: RecordedJudge | None, *, fracs: Sequence[float] = (0.1, 0.2, 0.3)) -> dict[str, object]:
    """데이터셋마다(그리고 redteam+gray 를 합친 '독립' 풀) 변형별 큐의 오류 회수율을 무작위 기대치와 견준다."""
    pools = {**sets, "independent": [*sets["redteam"], *sets["gray"]]}
    out: dict[str, object] = {}
    for name, items in pools.items():
        per: dict[str, object] = {}
        for variant in VARIANTS:
            if variant == "engine+cues+judge" and (judge is None or not all(judge.has(e.creative) for e in items)):
                continue                                                  # judge 녹음이 소재를 모두 덮을 때만 잰다
            ranked = rank(items, variant, judge)
            row: dict[str, object] = {"n": len(ranked), "errors": sum(r.error for r in ranked), "unsafe_approves": sum(r.unsafe_approve for r in ranked), "auc_error": auc(ranked, lambda r: r.error), "auc_unsafe": auc(ranked, lambda r: r.unsafe_approve)}
            for f in fracs:
                k, tot = recall_at(ranked, f, lambda r: r.error)
                uk, utot = recall_at(ranked, f, lambda r: r.unsafe_approve)
                row[f"top{round(f * 100)}"] = {
                    "errors_found": k, "errors_total": tot, "unsafe_found": uk, "unsafe_total": utot,
                    "random_error_recall": random_recall(ranked, f, lambda r: r.error), "random_unsafe_recall": random_recall(ranked, f, lambda r: r.unsafe_approve),
                }
            per[variant] = row
        out[name] = per
    return out
