"""후보의 결정론 품질 신호(정성 품질을 *측정 가능한 신호*로 옮긴 것). LLM 심판이 아니라 사실에 대한 숫자다.

* ``coverage``: 중요도 가중으로, 쓸 수 있는 사실 중 후보가 인용한 비율(상위 몇 개를 다 쓰면 만점).
* ``specificity``: 제목·본문에 구체적 수치(숫자 토큰)가 몇 개 있는가(3개에서 만점).
* ``length_fit``: 제목이 너무 짧거나 길지 않고 본문이 한 줄 이상인가.

가중 합은 추천 순서를 정하는 데만 쓴다. 이 점수가 '좋은 광고'를 뜻한다고 주장하지 않는다 — 별도의 쌍대 비교(creative.compare)와 독립 감사로 이 신호가 실제 품질과 맞는지 잰다.
"""

from __future__ import annotations

from collections.abc import Mapping

from .models import Candidate, Constraints, Fact
from .products import BrandVoice
from .verify import digit_tokens

WEIGHT = {1: 3, 2: 2, 3: 1}


def signals(c: Candidate, facts: Mapping[str, Fact], voice: BrandVoice, cons: Constraints) -> dict[str, float]:
    cited = [facts[i] for i in dict.fromkeys(c.fact_ids) if i in facts]
    total = sum(WEIGHT[f.importance] for f in facts.values()) or 1
    got = sum(WEIGHT[f.importance] for f in cited)
    coverage = min(1.0, got / min(total, 9))
    specificity = min(1.0, len(digit_tokens(f"{c.headline} {c.body}")) / 3)
    head_max = min(cons.max_headline_chars, voice.max_headline_chars)
    length_fit = 1.0 if (8 <= len(c.headline) <= head_max and len(c.body) >= 20) else 0.5
    return {"coverage": round(coverage, 4), "specificity": round(specificity, 4), "length_fit": length_fit}


def score(c: Candidate, facts: Mapping[str, Fact], voice: BrandVoice, cons: Constraints) -> float:
    s = signals(c, facts, voice, cons)
    return round(0.55 * s["coverage"] + 0.25 * s["specificity"] + 0.2 * s["length_fit"], 4)
