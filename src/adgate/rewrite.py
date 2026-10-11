"""수정안 생성과 재심사. 생성기는 바뀔 수 있어도(규칙 → LLM) **검증 게이트는 그대로**다.

    반려/보류 소재 → (자동으로 고칠 수 있는 위반만) 수정안 → 같은 파이프라인으로 재심사 → 사실 보존 검사

수정안이 '검증됨(verified)'이 되려면 재심사에서 APPROVE 이고, 원문의 수치 주장이 지워지거나 새로 생기지 않아야 하며,
고치지 않은 단어가 대부분 남아 있어야 한다. 자동으로 고칠 수 없는 위반(질병 표방, 랜딩 불일치, 제한 업종, 사기성 유도,
광고주 인증)은 건드리지 않고 광고주 입력이 필요하다고 알린다.

이 모듈의 생성기는 템플릿(결정론)이다. 문장을 잘 쓰는 것이 목적이 아니라 '생성 → 검증' 고리를 보이는 것이 목적이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from . import numbers as nums
from .config import ReviewConfig
from .models import AD_FIELDS, Creative, Finding, ReviewResult
from .normalize import build
from .pipeline import review
from .policy import Policy, load_policy

AUTO_FIXABLE = frozenset({"AD-ABS-01", "AD-GUA-01", "AD-CMP-01", "AD-URG-01", "AD-ENDO-01", "AD-FIN-01"})
RETENTION_MIN = 0.8
MIN_HEADLINE_CHARS = 4  # 공백을 뺀 글자 수. 이보다 짧아지면 광고 제목으로 쓸 수 없다고 본다

_SCOPE_BEFORE = re.compile(r"(?:국내|업계|세계|전국|대한민국|아시아|동종업계|업종|지역|시중)\s*$")
_SUFFIX_AFTER = re.compile(r"^(?:으로|하는|인|의|한|급|된|은|는|이|가|을|를|에|로|도|만|합니다|해요|해 드립니다|드립니다|해드립니다)?")
_FOLLOW_NUMBER = re.compile(r"^\s*\d[\d,.]*\s*(?:배|%|퍼센트|프로)?")

_GUARANTEE_TAIL = re.compile(r"^\s*(?:보장|보증|확약|약속)(?:형|합니다|해요|해 드립니다|드립니다|됩니다)?")
_DELETE_PATTERNS = frozenset({"no_loss", "must"})  # 문장을 이어 붙일 수 없는 형용사형 표현은 지운다

_APPROVAL_WORDS = re.compile(r"승인|통과|대출|지급|환급")
_MONEY_WORDS = re.compile(r"수익|이익|이자|배당|원금|이득|손실|손해|리스크|위험|마이너스")
_REPL_APPROVAL = "승인 여부는 심사 후 결정"
_REPL_MONEY = "성과는 변동될 수 있음"
_REPL_RESULT = "결과는 개인별로 다름"


class AppliedFix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    field: str
    before: str
    after: str


class Verification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome_after: str
    passes: bool
    retention: float
    lost_claims: list[str]
    new_claims: list[str]
    thin_headline: bool
    problems: list[str] = []  # 문장이 깨졌는지 보는 검사: mid_token_cut, unbalanced_brackets, unbalanced_quotes, lost_digits, too_little_left, headline_replaced


class RewriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["verified", "partial", "unavailable"]
    candidate: Creative | None
    applied: list[AppliedFix]
    unresolved: list[str]  # 남은 위반(재심사 기준)을 '규칙:코드' 로
    verification: Verification | None


@dataclass
class _Edit:
    field: str
    start: int
    end: int
    replacement: str
    rule_id: str


def _tidy(text: str) -> str:
    t = re.sub(r"\s{2,}", " ", text)
    t = re.sub(r"\s+([,.!?])", r"\1", t)
    t = re.sub(r"^[\s,.!?·]+", "", t)
    t = re.sub(r"([,]\s*)+([.!?])", r"\2", t)
    t = re.sub(r"[\s,]+$", "", t) if not re.search(r"[.!?]$", t) else t
    return t.strip()


def _edit_for(creative: Creative, f: Finding) -> _Edit | None:
    if f.field is None or f.start is None or f.end is None or f.field not in AD_FIELDS:
        return None
    text = creative.text(f.field)
    start, end = f.start, f.end
    if f.rule_id == "AD-GUA-01":
        # 동사 어미·'보장' 꼬리까지 단어 전체를 바꾼다(수익을 보장합니다 / 100% 승인 보장 → 조건부 표현).
        guar = _GUARANTEE_TAIL.match(text[end:])
        if guar:
            end += guar.end()
        while end < len(text) and not text[end].isspace() and text[end] not in ",.!?":
            end += 1
        quote_tight = build(text[start:end]).tight
        pattern = (f.note or "").removeprefix("pattern:")
        if pattern in _DELETE_PATTERNS:
            repl = ""
        elif _APPROVAL_WORDS.search(quote_tight):
            repl = _REPL_APPROVAL
        elif _MONEY_WORDS.search(quote_tight):
            repl = _REPL_MONEY
        else:
            repl = _REPL_RESULT
        if repl == "":
            while end < len(text) and text[end] == " ":
                end += 1
        return _Edit(f.field, start, end, repl, f.rule_id)
    if f.rule_id in {"AD-ABS-01", "AD-CMP-01", "AD-URG-01", "AD-ENDO-01"}:
        if f.rule_id == "AD-ABS-01" and f.code == "substantiated_needs_check":
            return None  # 근거를 밝힌 표현은 사람이 확인한다. 지우지 않는다.
        m = _SCOPE_BEFORE.search(text[:start])
        if m and f.rule_id == "AD-ABS-01":
            start = m.start()
        tail = text[end:]
        if f.rule_id in {"AD-CMP-01", "AD-ENDO-01"}:
            n = _FOLLOW_NUMBER.match(tail)
            if n:
                end += n.end()
                tail = text[end:]
        suf = _SUFFIX_AFTER.match(tail)
        if suf:
            end += suf.end()
        while end < len(text) and text[end] == " ":
            end += 1
        return _Edit(f.field, start, end, "", f.rule_id)
    return None


def _apply(creative: Creative, edits: list[_Edit]) -> tuple[dict[str, str], list[AppliedFix]]:
    by_field: dict[str, list[_Edit]] = {}
    for e in edits:
        by_field.setdefault(e.field, []).append(e)
    updates: dict[str, str] = {}
    applied: list[AppliedFix] = []
    for field, es in by_field.items():
        text = creative.text(field)
        spans: list[_Edit] = []
        for e in sorted(es, key=lambda x: x.start):
            if spans and e.start < spans[-1].end:
                continue  # 겹치는 수정은 앞의 것만
            spans.append(e)
        for e in sorted(spans, key=lambda x: x.start, reverse=True):
            applied.append(AppliedFix(rule_id=e.rule_id, field=field, before=text[e.start:e.end], after=e.replacement))
            text = text[:e.start] + e.replacement + text[e.end:]
        updates[field] = _tidy(text)
    applied.reverse()
    return updates, applied


def _fin_text(result: ReviewResult, policy: Policy) -> str | None:
    fin = next((f for f in result.findings if f.rule_id == "AD-FIN-01" and f.missing), None)
    if fin is None or fin.missing is None:
        return None
    catalog = policy.rule("AD-FIN-01").missing
    return " ".join(catalog[k]["text"] for k in fin.missing)


def _claims(creative: Creative) -> set[str]:
    out: set[str] = set()
    for f in ("headline", "body", "cta", "image_text"):
        for c in nums.extract(creative.text(f).lower()):
            out.add(f"{c.kind}:{c.value}")
    return out


_TOKEN = re.compile(r"[0-9a-z가-힣]{2,}")
_DIGITS = re.compile(r"\d+")
_OPEN, _CLOSE = "([{「『【〈《", ")]}」』】〉》"
MIN_TOKENS_LEFT = 2  # 위반 표현을 빼고 원문 낱말이 이보다 적게 남으면 메시지가 사라진 것이다


def _balanced(text: str) -> bool:
    stack: list[str] = []
    for ch in text:
        if ch in _OPEN:
            stack.append(_CLOSE[_OPEN.index(ch)])
        elif ch in _CLOSE:
            if not stack or stack.pop() != ch:
                return False
    return not stack


def _syntax_problems(creative: Creative, candidate: Creative, edits: list[_Edit], orig_tokens: set[str]) -> list[str]:
    """'재심사 통과 + 수치 보존' 만으로는 깨진 문장이 검증됨이 된다(독립 리뷰 M8). 생성기가 낸 흔한 파손을 따로 본다."""
    problems: list[str] = []
    for e in edits:
        text = creative.text(e.field)
        before = text[e.start - 1] if e.start > 0 else " "
        after = text[e.end] if e.end < len(text) else " "
        # 수정 구간이 낱말 한가운데서 시작하거나 끝나면 조사·어미 꼬리가 남는다("유일하게" → "하게", "5가지" → "지").
        if (before.isalnum() and text[e.start].isalnum()) or (after.isalnum() and text[e.end - 1].isalnum() and not e.replacement):
            problems.append("mid_token_cut")
            break
    changed = [f for f in AD_FIELDS if creative.text(f) != candidate.text(f)]
    if any(not _balanced(candidate.text(f)) for f in changed):
        problems.append("unbalanced_brackets")
    if any(candidate.text(f).count('"') % 2 or (candidate.text(f).count("“") != candidate.text(f).count("”")) for f in changed):
        problems.append("unbalanced_quotes")
    removed = [(e.field, e.start, e.end) for e in edits]
    kept_digits: set[str] = set()
    for f in AD_FIELDS:
        text = creative.text(f)
        for fld, s, en in sorted(removed, key=lambda x: -x[1]):
            if fld == f:
                text = text[:s] + " " + text[en:]
        kept_digits.update(_DIGITS.findall(text))
    cand_digits = {d for f in AD_FIELDS for d in _DIGITS.findall(candidate.text(f))}
    if kept_digits - cand_digits:
        problems.append("lost_digits")
    if len(orig_tokens) < MIN_TOKENS_LEFT:
        problems.append("too_little_left")
    head = creative.headline
    for fld, s, en in sorted(removed, key=lambda x: -x[1]):
        if fld == "headline":
            head = head[:s] + " " + head[en:]
    if _TOKEN.findall(creative.headline.lower()) and not _TOKEN.findall(head.lower()):
        problems.append("headline_replaced")  # 제목의 원문 낱말이 하나도 남지 않았다 — 템플릿 문구만 남은 제목은 수정안이 아니다
    return problems


def _tokens(creative: Creative, exclude: list[tuple[str, int, int]] | None = None) -> set[str]:
    toks: set[str] = set()
    for f in AD_FIELDS:
        text = creative.text(f)
        if exclude:
            for fld, s, e in sorted(exclude, key=lambda x: -x[1]):
                if fld == f:
                    text = text[:s] + " " + text[e:]
        toks.update(_TOKEN.findall(text.lower()))
    return toks


def rewrite(
    creative: Creative, result: ReviewResult, cfg: ReviewConfig | None = None, policy: Policy | None = None,
) -> RewriteResult:
    cfg = cfg or ReviewConfig()
    policy = policy or load_policy()
    actionable = [f for f in result.findings if f.severity in ("reject", "review") and f.source == "rule"]
    edits = [e for f in actionable if f.rule_id in AUTO_FIXABLE and (e := _edit_for(creative, f)) is not None]
    updates, applied = _apply(creative, edits)

    add = _fin_text(result, policy)
    if add is not None:
        before = updates.get("disclosure", creative.disclosure or "").strip()
        updates["disclosure"] = f"{before} {add}".strip()
        applied.append(AppliedFix(rule_id="AD-FIN-01", field="disclosure", before=before, after=updates["disclosure"]))

    if not applied:
        return RewriteResult(status="unavailable", candidate=None, applied=[], unresolved=[f"{f.rule_id}:{f.code}" for f in actionable], verification=None)

    headline = updates.get("headline", creative.headline)
    if not headline.strip():
        return RewriteResult(status="unavailable", candidate=None, applied=applied, unresolved=["headline_empty_after_fix"], verification=None)

    candidate = creative.model_copy(update={**updates, "disclosure": updates.get("disclosure", creative.disclosure) or None})
    try:  # model_copy 는 검증하지 않는다: 덧붙인 고지가 길이 제한(2000자)을 넘을 수 있다
        Creative.model_validate(candidate.model_dump())
    except ValueError:
        return RewriteResult(status="unavailable", candidate=None, applied=applied, unresolved=["candidate_invalid"], verification=None)
    after = review(candidate, cfg, policy)

    removed = [(e.field, e.start, e.end) for e in edits]
    orig_tokens = _tokens(creative, removed)
    cand_tokens = _tokens(candidate)
    retention = round(len(orig_tokens & cand_tokens) / len(orig_tokens), 4) if orig_tokens else 1.0
    orig_claims, cand_claims = _claims(creative), _claims(candidate)
    # 위반 인용 안에 있던 수치는 지워져도 된다. 그 밖의 수치가 사라지거나 새로 생기면 안 된다.
    removed_claims: set[str] = set()
    for a in applied:
        if a.rule_id != "AD-FIN-01":
            removed_claims |= {f"{c.kind}:{c.value}" for c in nums.extract(a.before.lower())}
    lost = sorted((orig_claims - cand_claims) - removed_claims)
    new = sorted(cand_claims - orig_claims)
    passes = after.outcome == "APPROVE"
    thin = len(candidate.headline.replace(" ", "")) < MIN_HEADLINE_CHARS
    problems = _syntax_problems(creative, candidate, edits, orig_tokens)
    verified = passes and retention >= RETENTION_MIN and not lost and not new and not thin and not problems
    return RewriteResult(
        status="verified" if verified else "partial",
        candidate=candidate,
        applied=applied,
        unresolved=[f"{f.rule_id}:{f.code}" for f in after.findings if f.severity in ("reject", "review")],
        verification=Verification(
            outcome_after=after.outcome, passes=passes, retention=retention, lost_claims=lost, new_claims=new,
            thin_headline=thin, problems=problems,
        ),
    )
