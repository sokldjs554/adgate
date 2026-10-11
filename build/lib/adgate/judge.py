"""의미 기반 검사(Judge) 인터페이스와 근거 검증.

Judge(보통 LLM)는 **위반 후보를 제안만** 한다. 제안에는 판정 필드가 없다.
제안은 아래 검증을 모두 통과해야 발견(Finding)이 된다.

1. 규칙 ID·코드가 정책에 있고, 이 업종에 적용되는 규칙이다.
2. 필드가 존재하고, 인용이 그 필드 원문 안에 실제로 있다(정규화 후 일치도 허용하되 원문 위치로 되돌린다).
3. 심각도는 정책이 정한다(Judge가 정하지 못한다).

통과하지 못한 제안은 버리고 개수만 센다. Judge는 기존 발견을 지울 수 없고 더할 수만 있다.
Judge 호출이 실패하면 호출한 쪽(pipeline)이 fail-closed 로 처리한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .models import AD_FIELDS, Creative, Finding
from .normalize import build
from .policy import Policy

MAX_PROPOSALS = 20


class ProposedFinding(BaseModel):
    """Judge 가 내놓는 후보. verdict·outcome·severity 필드가 없다(추가 키는 거부)."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    code: str
    field: str
    quote: str = Field(min_length=1, max_length=500)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    rationale: str | None = Field(default=None, max_length=500)


class Judge(Protocol):
    name: str

    def propose(
        self, creative: Creative, policy: Policy, already: Sequence[dict[str, Any]] = ...,
    ) -> Sequence[ProposedFinding]: ...


def locate(original: str, quote: str) -> tuple[int, int] | None:
    """quote 가 original 안 어디인지. 정확히 있으면 그 위치, 없으면 정규화한 뒤 찾아 원문 위치로 되돌린다."""
    idx = original.find(quote)
    if idx >= 0:
        return idx, idx + len(quote)
    nv = build(original)
    tq = build(quote).tight
    if len(tq) < 2:
        return None
    pos = nv.tight.find(tq)
    if pos < 0:
        return None
    return nv.orig_span_from_tight(pos, pos + len(tq))


def ground(proposals: Sequence[ProposedFinding], creative: Creative, policy: Policy) -> tuple[list[Finding], int]:
    """제안 목록을 검증해 (발견, 버린 개수)를 돌려준다."""
    kept: list[Finding] = []
    dropped = 0
    category = creative.advertiser.category
    for p in list(proposals)[:MAX_PROPOSALS]:
        rule = policy.rules.get(p.rule_id)
        if (
            rule is None
            or p.code not in rule.codes
            or policy.spec(p.rule_id, p.code).severity == "info"
            or not policy.applies(p.rule_id, category)
            or p.field not in AD_FIELDS
        ):
            dropped += 1
            continue
        text = creative.text(p.field)
        span = locate(text, p.quote) if text else None
        if span is None:
            dropped += 1
            continue
        s, e = span
        kept.append(
            Finding(
                rule_id=p.rule_id, code=p.code, severity=policy.spec(p.rule_id, p.code).severity,
                field=p.field, quote=text[s:e], start=s, end=e, source="llm",
                note=p.rationale, confidence=p.confidence,
            )
        )
    dropped += max(0, len(proposals) - MAX_PROPOSALS)
    return kept, dropped


class ScriptedJudge:
    """테스트용 Judge. 정해 둔 제안을 그대로 돌려주거나, 호출 시 예외를 던진다."""

    name = "scripted"

    def __init__(self, proposals: Sequence[ProposedFinding] = (), *, raises: Exception | None = None) -> None:
        self.proposals = list(proposals)
        self.raises = raises
        self.calls = 0
        self.last_already: list[dict[str, Any]] = []

    def propose(
        self, creative: Creative, policy: Policy, already: Sequence[dict[str, Any]] = (),
    ) -> Sequence[ProposedFinding]:
        self.calls += 1
        self.last_already = list(already)
        if self.raises is not None:
            raise self.raises
        return self.proposals
