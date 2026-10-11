from __future__ import annotations

import pytest

from adgate.config import ReviewConfig
from adgate.models import Advertiser, Creative
from adgate.pipeline import review


def mk(
    headline: str = "", body: str = "", cta: str = "자세히 보기", *, disclosure: str | None = None,
    image_text: str | None = None, landing: str | None = None, category: str = "general", verified: bool = True,
    ad_id: str = "t",
) -> Creative:
    return Creative(
        ad_id=ad_id,
        advertiser=Advertiser(name="가상광고주", category=category, verified=verified),  # type: ignore[arg-type]
        headline=headline, body=body, cta=cta, disclosure=disclosure, image_text=image_text, landing_text=landing,
    )


def codes(creative: Creative, cfg: ReviewConfig | None = None) -> set[tuple[str, str]]:
    """(rule_id, code) 집합. info 등급은 제외. 도전자는 끈 상태(규칙만 검사)."""
    cfg = cfg or ReviewConfig(challenge=False)
    result = review(creative, cfg)
    return {(f.rule_id, f.code) for f in result.findings if f.severity != "info"}


@pytest.fixture
def run():
    return review
