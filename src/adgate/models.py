"""도메인 모델. 입력은 엄격하게(extra=forbid, 길이 제한) 받는다."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal[
    "loan", "invest", "supplement", "cosmetic", "ecommerce",
    "app", "education", "general", "adult", "gambling",
]
Severity = Literal["reject", "review", "info"]
Outcome = Literal["APPROVE", "REJECT", "REVIEW"]
Source = Literal["rule", "challenge", "llm"]

# 위반 표현을 찾는 필드. landing_text는 일부 규칙에서만 쓴다.
AD_FIELDS: tuple[str, ...] = ("headline", "body", "cta", "image_text", "disclosure")
ALL_TEXT_FIELDS: tuple[str, ...] = (*AD_FIELDS, "landing_text")

MAX_AD_FIELD = 2000
MAX_LANDING = 20000


class Advertiser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=100)
    category: Category
    verified: bool = False


class Creative(BaseModel):
    """광고 소재 한 건. 텍스트는 모두 신뢰하지 않는 입력(데이터)이다."""

    model_config = ConfigDict(extra="forbid")

    ad_id: str = Field(max_length=100)
    advertiser: Advertiser
    headline: str = Field(default="", max_length=MAX_AD_FIELD)
    body: str = Field(default="", max_length=MAX_AD_FIELD)
    cta: str = Field(default="", max_length=MAX_AD_FIELD)
    disclosure: str | None = Field(default=None, max_length=MAX_AD_FIELD)
    image_text: str | None = Field(default=None, max_length=MAX_AD_FIELD)
    landing_text: str | None = Field(default=None, max_length=MAX_LANDING)

    def text(self, field: str) -> str:
        if field not in ALL_TEXT_FIELDS:
            raise KeyError(field)
        return getattr(self, field) or ""


class Finding(BaseModel):
    """규칙이 찾아낸 한 건. 인용이 있는 발견은 원문 필드의 [start:end]와 정확히 같아야 한다."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    code: str
    severity: Severity
    field: str | None = None
    quote: str | None = None
    start: int | None = None
    end: int | None = None
    source: Source = "rule"
    note: str | None = None
    missing: list[str] | None = None
    confidence: float | None = None


class Reason(BaseModel):
    """광고주에게 보여 줄 반려·보류 사유 한 줄."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    code: str
    severity: Severity
    field: str | None
    quote: str | None
    title: str
    message: str
    fix: str


class ReviewStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latency_ms: float = 0.0
    dropped_ungrounded: int = 0
    judge_called: bool = False
    judge_failed: bool = False


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ad_id: str
    outcome: Outcome
    findings: list[Finding]
    reasons: list[Reason]
    policy_version: str
    escalations: list[str] = Field(default_factory=list)
    stats: ReviewStats = Field(default_factory=ReviewStats)
