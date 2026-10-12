"""소재 생성 파이프라인의 자료형."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models import Advertiser, Creative
from .products import BrandVoice

ALLOWED_CTAS = (
    "자세히 보기", "구매하기", "지금 구매", "신청하기", "시작하기", "더 알아보기", "예약하기", "설치하기", "문의하기", "가입하기", "체험하기",
)
FactKind = Literal["attribute", "price", "promo", "spec", "shipping", "policy", "review", "other"]


class Fact(BaseModel):
    """페이지(또는 상품 이미지)에서 글자 그대로 뽑은 사실. ``text`` 는 원문의 연속된 부분 문자열이다."""

    model_config = ConfigDict(extra="forbid")

    id: str
    text: str = Field(min_length=2, max_length=120)
    source: Literal["page", "image"]
    kind: FactKind
    importance: Literal[1, 2, 3] = 2


class ImageBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layout: Literal["hero", "badge"]
    overlay_text: str = Field(min_length=1, max_length=24)
    badge_text: str | None = Field(default=None, max_length=10)


class Candidate(BaseModel):
    """광고 후보. ``fact_ids`` 는 이 후보의 모든 주장이 나온 사실이다."""

    model_config = ConfigDict(extra="forbid")

    headline: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=400)
    cta: str = Field(min_length=1, max_length=40)
    disclosure: str | None = Field(default=None, max_length=300)
    fact_ids: list[str] = Field(max_length=14)
    image: ImageBrief


class Constraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_headline_chars: int = 30
    max_body_chars: int = 120
    max_overlay_chars: int = 20
    max_badge_chars: int = 8
    ctas: tuple[str, ...] = ALLOWED_CTAS
    n_candidates: int = 3
    repair_rounds: int = 2


class Problem(BaseModel):
    """후보가 막힌 이유 하나. 심사 사유이거나 결정론 검증 실패다."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["policy", "verify"]
    code: str
    message: str
    quote: str | None = None
    fix: str | None = None


class Attempt(BaseModel):
    """후보 하나의 이력: 처음 쓴 것과 수리한 것들, 각 시도의 결과."""

    model_config = ConfigDict(extra="forbid")

    candidate: Candidate
    problems: list[Problem] = Field(default_factory=list)
    outcome: Literal["APPROVE", "REVIEW", "REJECT"] = "REJECT"
    creative: Creative | None = None


class CandidateTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempts: list[Attempt]
    accepted: bool
    final_status: Literal["approved", "landing_review", "exhausted", "blocked_by_account"]


class ProductResult(BaseModel):
    """상품 하나를 처리한 결과: 중간 산출물과 후보별 이력, 추천."""

    model_config = ConfigDict(extra="forbid")

    product_id: str
    advertiser: Advertiser
    brand_voice: BrandVoice
    image_texts: list[str] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    screened_out: list[dict[str, str]] = Field(default_factory=list)    # 사실 심사에서 걸러진 것 {text, reason}
    ungrounded_facts: int = 0                                          # 인용이 원문에 없어 버려진 추출 사실 수
    risky_claims: list[str] = Field(default_factory=list)
    injection_flagged: list[str] = Field(default_factory=list)         # 결정론 정화가 걸러 낸 문장
    hidden_dropped: int = 0                                            # 파서가 숨겨진 텍스트로 뺀 조각 수
    traces: list[CandidateTrace] = Field(default_factory=list)
    recommended: list[int] = Field(default_factory=list)               # 승인된 후보의 traces 인덱스(점수 순)
    invalid_candidates: int = 0                                        # 형식이 어긋나 버려진 후보 수
    render_checks: list[dict[str, Any]] = Field(default_factory=list)  # 그린 이미지를 다시 읽은 왕복 검사
    calls: dict[str, int] = Field(default_factory=dict)                # 과제별 LLM 호출 수
    status: Literal["ok", "landing_review", "no_safe_candidate", "blocked_by_account", "failed"] = "ok"
    error: str | None = None
