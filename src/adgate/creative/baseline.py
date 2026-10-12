"""기준선 B0: 템플릿 생성기(LLM 없음). 같은 검증·심사 게이트를 통과시켜 LLM 생성과 견준다.

사실 중 중요도가 높은 것을 그대로 이어 붙인다. 문장은 거칠지만 사실에서 벗어나지 않는다 — LLM 생성이 *더하는 것*(자연스러움, 구체성)과
*더하는 위험*(지어내기, 위반 표현)을 같은 자로 재기 위한 대조군이다.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..models import Advertiser
from .models import Candidate, Constraints, Fact, ImageBrief
from .products import BrandVoice

DEFAULT_CTA = {
    "loan": "신청하기", "invest": "시작하기", "supplement": "자세히 보기", "cosmetic": "자세히 보기", "ecommerce": "구매하기",
    "app": "설치하기", "education": "체험하기", "general": "자세히 보기", "adult": "자세히 보기", "gambling": "자세히 보기",
}
STANDARD_DISCLOSURE = {
    "loan": "대출은 신용점수에 영향을 줄 수 있으며 금리는 신용도에 따라 달라질 수 있습니다.",
    "invest": "투자 상품은 원금 손실이 발생할 수 있으며 과거 성과가 미래 수익을 보장하지 않습니다.",
}


def _trim(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def template_candidates(advertiser: Advertiser, voice: BrandVoice, facts: Sequence[Fact], cons: Constraints) -> list[Candidate]:
    usable = sorted((f for f in facts if f.kind != "review"), key=lambda f: (f.importance, f.id))
    if not usable:
        return []
    brand = voice.required_terms[0] if voice.required_terms else advertiser.name
    head_max = min(cons.max_headline_chars, voice.max_headline_chars)
    groups = [usable[0:2], usable[1:3] or usable[0:1], usable[2:4] or usable[0:1]][: cons.n_candidates]
    out: list[Candidate] = []
    for g in groups:
        headline = _trim(f"{brand} {g[0].text}", head_max)
        body = _trim(" · ".join(f.text for f in g), cons.max_body_chars)
        out.append(Candidate(
            headline=headline, body=body, cta=DEFAULT_CTA.get(advertiser.category, "자세히 보기"),
            disclosure=STANDARD_DISCLOSURE.get(advertiser.category), fact_ids=[f.id for f in g],
            image=ImageBrief(layout="hero", overlay_text=_trim(g[0].text, cons.max_overlay_chars)),
        ))
    return out
