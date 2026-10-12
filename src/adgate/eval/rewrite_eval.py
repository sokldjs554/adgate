"""수정안(생성 → 검증 고리) 평가 하니스. 사전등록: docs/EVAL_REWRITE.md.

엔진이 반려·보류한 소재마다 ``rewrite()`` 를 한 번 돌려 상태(verified·partial·unavailable), 막힌 검사, 세탁(검증됨인데 정답 위반 인용이 그대로 남음)을 센다.
독립 라벨러가 필요한 지표(정책 적합·충실·문장)는 여기서 내지 않는다 — 이 모듈은 라벨러에게 줄 *후보 텍스트*와 결정론 지표만 만든다.
모델을 부르지 않고 엔진·생성기·게이트를 바꾸지 않는다(읽기만 한다).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from ..config import ReviewConfig
from ..models import Creative, ReviewResult
from ..pipeline import review
from ..policy import Policy, load_policy
from ..rewrite import AUTO_FIXABLE, RETENTION_MIN, RewriteResult, rewrite
from .dataset import Example
from .metrics import ratio

# 검증 게이트의 검사(rewrite.py 의 verified 조건과 같다). 하나라도 어긋나면 partial 이다.
CHECKS = ("outcome", "retention", "lost_claims", "new_claims", "thin_headline", "syntax")
# 정책에서 자동으로 고치지 않기로 한 규칙(rewrite.AUTO_FIXABLE 의 여집합).
NONFIXABLE = ("AD-HLT-01", "AD-LND-01", "AD-BRD-01", "AD-FRD-01", "AD-ADV-01")


def failed_checks(rw: RewriteResult) -> list[str]:
    """후보가 있는 수정안에서 어긋난 검사. 검증됨이면 빈 목록."""
    v = rw.verification
    if rw.candidate is None or v is None:
        return []
    out: list[str] = []
    if not v.passes:
        out.append("outcome")
    if v.retention < RETENTION_MIN:
        out.append("retention")
    if v.lost_claims:
        out.append("lost_claims")
    if v.new_claims:
        out.append("new_claims")
    if v.thin_headline:
        out.append("thin_headline")
    if v.problems:
        out.append("syntax")
    return out


def laundered_quotes(ex: Example, candidate: Creative) -> list[dict[str, str]]:
    """수정안의 같은 필드에 *그대로 남아 있는* 정답 위반 인용. 인용 없는 위반(부재형)은 세지 않는다."""
    out: list[dict[str, str]] = []
    for v in ex.gold.violations:
        if v.field and v.quote and v.quote in candidate.text(v.field):
            out.append({"rule_id": v.rule_id, "field": v.field, "quote": v.quote})
    return out


def _unavailable_reason(rw: RewriteResult, fixable_findings: bool) -> str | None:
    if rw.status != "unavailable":
        return None
    if rw.unresolved == ["headline_empty_after_fix"]:
        return "headline_empty"
    if rw.unresolved == ["candidate_invalid"]:
        return "candidate_invalid"
    return "no_edit" if fixable_findings else "no_fixable_finding"


def evaluate_example(name: str, ex: Example, cfg: ReviewConfig, policy: Policy) -> dict[str, Any] | None:
    """엔진이 반려·보류하지 않은 소재는 None(모집단 밖)."""
    result: ReviewResult = review(ex.creative, cfg, policy)
    if result.outcome == "APPROVE":
        return None
    actionable = [f for f in result.findings if f.severity in ("reject", "review") and f.source == "rule"]
    engine_rules = sorted({f.rule_id for f in actionable})
    fixable = [r for r in engine_rules if r in AUTO_FIXABLE]
    rw = rewrite(ex.creative, result, cfg, policy)
    checks = failed_checks(rw)
    return {
        "id": ex.id, "set": name, "gold": ex.gold.decision, "gold_rules": sorted({v.rule_id for v in ex.gold.violations}),
        "engine": result.outcome, "engine_rules": engine_rules, "fixable_engine_rules": fixable,
        "status": rw.status, "unavailable_reason": _unavailable_reason(rw, bool(fixable)),
        "applied": [a.model_dump() for a in rw.applied], "unresolved": rw.unresolved,
        "verification": rw.verification.model_dump() if rw.verification else None,
        "failed_checks": checks,
        "laundered": laundered_quotes(ex, rw.candidate) if rw.status == "verified" and rw.candidate is not None else [],
        "candidate": rw.candidate.model_dump(mode="json") if rw.candidate is not None else None,
        "original": ex.creative.model_dump(mode="json"),
    }


def _status_block(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(items)
    c = Counter(i["status"] for i in items)
    return {"n": n, **{k: ratio(c[k], n) for k in ("verified", "partial", "unavailable")}}


def summarize(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """항목 목록의 결정론 지표. 참 반려(정답이 APPROVE 가 아님)와 오반려(정답 APPROVE)를 갈라 센다(R4)."""
    true_flag = [i for i in items if i["gold"] != "APPROVE"]
    false_flag = [i for i in items if i["gold"] == "APPROVE"]
    partial = [i for i in true_flag if i["status"] == "partial"]
    verified = [i for i in true_flag if i["status"] == "verified"]
    unavailable = [i for i in true_flag if i["status"] == "unavailable"]
    reasons = Counter(c for i in partial for c in i["failed_checks"])
    only = Counter(i["failed_checks"][0] for i in partial if len(i["failed_checks"]) == 1)
    only_nonfixable = [i for i in unavailable if not i["fixable_engine_rules"]]
    laundered = [i for i in verified if i["laundered"]]
    nonfixable_gold = [i for i in verified if set(i["gold_rules"]) & set(NONFIXABLE)]
    by_rule: dict[str, dict[str, Any]] = {}
    for rule in sorted({r for i in true_flag for r in i["gold_rules"]}):
        xs = [i for i in true_flag if rule in i["gold_rules"]]
        by_rule[rule] = _status_block(xs)
    return {
        "flagged": len(items),
        "true_flag": _status_block(true_flag),
        "false_flag": {**_status_block(false_flag), "with_candidate": ratio(sum(i["status"] != "unavailable" for i in false_flag), len(false_flag))},
        "partial_failed_checks": {c: reasons.get(c, 0) for c in CHECKS},
        "partial_blocked_only_by": {c: only.get(c, 0) for c in CHECKS},
        "unavailable_reasons": dict(Counter(i["unavailable_reason"] for i in unavailable)),
        "unavailable_only_nonfixable": ratio(len(only_nonfixable), len(unavailable)),
        "laundered": {**ratio(len(laundered), len(verified)), "ids": [i["id"] for i in laundered]},
        "verified_with_nonfixable_gold": {**ratio(len(nonfixable_gold), len(verified)), "ids": [i["id"] for i in nonfixable_gold]},
        "by_gold_rule": by_rule,
    }


def run(datasets: dict[str, list[Example]], cfg: ReviewConfig | None = None, policy: Policy | None = None) -> dict[str, Any]:
    """이름 → 예제 목록을 받아 셋별·합산 요약과 항목 상세를 낸다."""
    cfg = cfg or ReviewConfig()
    policy = policy or load_policy()
    per_set: dict[str, list[dict[str, Any]]] = {}
    sizes: dict[str, int] = {}
    for name, examples in datasets.items():
        sizes[name] = len(examples)
        per_set[name] = [rec for ex in examples if (rec := evaluate_example(name, ex, cfg, policy)) is not None]
    pooled = [i for items in per_set.values() for i in items]
    independent = [i for i in pooled if i["set"] in ("redteam", "gray")]
    return {
        "sizes": sizes,
        "by_set": {name: summarize(items) for name, items in per_set.items()},
        "pooled": summarize(pooled),
        "independent": summarize(independent),
        "items": pooled,
    }
