"""심사 파이프라인. 순수 함수 ``review`` 하나가 한 건을 처리한다(입출력 없음, 저장은 runtime 담당).

    정규화 → 엄격한 규칙 → 도전자(느슨한 두 번째 검사) → [Judge(선택)] → 병합 → 판정 → 사유

* 판정은 발견의 가장 높은 등급이다. 도전자와 Judge 는 발견을 **더할 수만** 있고 지울 수 없다.
* Judge 가 설정됐는데 실패하면 자동 승인하지 않는다(fail-closed): REVIEW 로 보낸다.
"""

from __future__ import annotations

import time
from typing import Any

from .challenge import challenge
from .config import ReviewConfig
from .decide import decide
from .explain import explain
from .judge import ground
from .models import Creative, Finding, ReviewResult, ReviewStats
from .policy import Policy, load_policy
from .rules import Scan, build_scan, resolve_overlaps, run_rules

_SEVERITY = {"info": 0, "review": 1, "reject": 2}
MAX_FINDINGS_OUT = 100  # 같은 표현을 수천 번 반복한 소재가 응답·저장소를 부풀리지 못하게 한다


def _merge_extra(strict: list[Finding], extra: list[Finding], policy: Policy) -> list[Finding]:
    """도전자·Judge 발견을 엄격한 발견에 붙인다. 이미 같은 자리를 더 무겁게(또는 같게) 판정했으면 버린다."""
    extra = resolve_overlaps(extra, policy)
    kept: list[Finding] = []
    for f in extra:
        if f.start is not None and f.end is not None:
            covered = any(
                s.field == f.field and s.start is not None and s.end is not None
                and f.start < s.end and s.start < f.end and _SEVERITY[s.severity] >= _SEVERITY[f.severity]
                for s in strict
            )
            if covered:
                continue
        kept.append(f)
    return strict + kept


def _is_grounded(f: Finding, creative: Creative) -> bool:
    """불변식: 인용이 있는 발견은 원문 필드의 [start:end] 와 정확히 같다."""
    if f.quote is None:
        return f.start is None and f.end is None
    if f.field is None or f.start is None or f.end is None:
        return False
    text = creative.text(f.field)
    return text[f.start:f.end] == f.quote


def _demote_ungrounded(findings: list[Finding], creative: Creative, stats: ReviewStats) -> list[Finding]:
    """인용 불변식이 깨진 발견은 버리지 않는다. 버리면 위반이 조용히 사라져 자동 승인된다(독립 리뷰 M1). 인용·위치만 지우고 세어 둔다."""
    out: list[Finding] = []
    for f in findings:
        if _is_grounded(f, creative):
            out.append(f)
            continue
        stats.quote_mismatch += 1
        note = f"{f.note}; quote_mismatch" if f.note else "quote_mismatch"
        out.append(f.model_copy(update={"quote": None, "start": None, "end": None, "note": note}))
    return out


def review(
    creative: Creative,
    cfg: ReviewConfig | None = None,
    policy: Policy | None = None,
    trace: list[dict[str, Any]] | None = None,
) -> ReviewResult:
    t0 = time.perf_counter()
    cfg = cfg or ReviewConfig()
    policy = policy or load_policy()
    stats = ReviewStats()

    def log(stage: str, **data: Any) -> None:
        if trace is not None:
            trace.append({"stage": stage, **data})

    scan: Scan = build_scan(creative, cfg, policy)
    log("normalize", mode="full" if cfg.normalize else "plain", fields=sorted(scan.views))

    strict = resolve_overlaps(run_rules(scan), policy)
    log("rules", count=len(strict), rules=sorted({f.rule_id for f in strict}))

    extra: list[Finding] = []
    if cfg.challenge:
        loose = challenge(scan, strict)
        extra.extend(loose)
        log("challenge", count=len(loose))

    escalations: list[str] = []
    if cfg.judge is not None:
        stats.judge_called = True
        try:
            already = [
                {"rule_id": f.rule_id, "field": f.field, "quote": f.quote}
                for f in (*strict, *extra) if f.severity != "info" and f.quote
            ]
            proposals = cfg.judge.propose(creative, policy, already)
            grounded, dropped = ground(proposals, creative, policy)
            if cfg.judge_cap_review:  # LLM 의 말만으로 자동 반려하지 않는다: 사람이 본다
                grounded = [f.model_copy(update={"severity": "review"}) if f.severity == "reject" else f for f in grounded]
            stats.dropped_ungrounded = dropped
            extra.extend(grounded)
            if dropped and cfg.escalate_ungrounded:  # 모델이 위반을 의심했지만 근거를 대지 못했다 = 애매하다 → 승인하지 않는다(POLICY 1절)
                escalations.append("judge_ungrounded")
                extra.append(Finding(
                    rule_id="SYS-JUDGE", code="judge_ungrounded", severity=policy.spec("SYS-JUDGE", "judge_ungrounded").severity,
                    note=f"dropped={dropped}",
                ))
            log("judge", proposed=len(proposals), kept=len(grounded), dropped_ungrounded=dropped)
        except Exception as exc:  # noqa: BLE001 — 어떤 실패든 fail-closed
            stats.judge_failed = True
            escalations.append("judge_unavailable")
            extra.append(
                Finding(
                    rule_id="SYS-JUDGE", code="judge_unavailable",
                    severity=policy.spec("SYS-JUDGE", "judge_unavailable").severity,
                    note=type(exc).__name__,
                )
            )
            log("judge", failed=type(exc).__name__)

    findings = _merge_extra(strict, extra, policy)
    findings = _demote_ungrounded(findings, creative, stats)  # 방어선: 인용이 어긋나면 *인용만* 지운다. 발견(등급)은 그대로 둔다
    outcome = decide(findings)  # 판정은 잘라 내기 전의 전체 발견으로 계산한다
    if len(findings) > MAX_FINDINGS_OUT:
        findings.sort(key=lambda f: -_SEVERITY[f.severity])  # 무거운 등급을 먼저 남긴다(안정 정렬)
        stats.findings_truncated = len(findings) - MAX_FINDINGS_OUT
        findings = findings[:MAX_FINDINGS_OUT]
    reasons = explain(findings, policy)
    stats.latency_ms = round((time.perf_counter() - t0) * 1000, 3)
    log("decide", outcome=outcome, reasons=len(reasons))

    return ReviewResult(
        ad_id=creative.ad_id, outcome=outcome, findings=findings, reasons=reasons,
        policy_version=policy.version, escalations=escalations, stats=stats,
    )
