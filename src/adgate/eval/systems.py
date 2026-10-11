"""평가 대상 시스템 어댑터. 파이프라인과 베이스라인을 같은 인터페이스(Creative → Prediction)로 감싼다."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from ..config import ReviewConfig
from ..models import AD_FIELDS, Creative, Finding, Outcome
from ..pipeline import review


@dataclass
class Prediction:
    outcome: Outcome
    findings: list[Finding]
    latency_ms: float
    quote_mismatch: int = 0  # 인용이 어긋나 지운 발견 수(grounding 지표가 이것을 '틀림'으로 센다)


System = Callable[[Creative], Prediction]


def pipeline_system(cfg: ReviewConfig | None = None) -> System:
    cfg = cfg or ReviewConfig()

    def run(creative: Creative) -> Prediction:
        r = review(creative, cfg)
        return Prediction(r.outcome, r.findings, r.stats.latency_ms, r.stats.quote_mismatch)

    return run


def all_to_human() -> System:
    """B0: 전부 사람에게 보낸다. 안전하지만 자동 처리율이 0 이다."""

    def run(creative: Creative) -> Prediction:
        return Prediction("REVIEW", [], 0.0)

    return run


# B1: 순진한 키워드 블록리스트. docs/POLICY.md 의 위반 예 단어를 그대로 옮겼다.
# 정규화·부정문·범위 표현·면책·업종 구분이 없고, 하나라도 들어 있으면 REJECT, 없으면 APPROVE 한다.
BLOCKLIST: dict[str, tuple[str, ...]] = {
    "AD-GUA-01": ("무조건 승인", "100% 승인", "수익 보장", "원금 보장", "확정 수익", "손실 없는", "한 달 만에", "3개월 만에 2배"),
    "AD-ABS-01": ("최고", "1위", "최초", "유일", "완벽", "100% 만족", "no.1"),
    "AD-HLT-01": ("치료", "완치", "예방"),
    "AD-CMP-01": ("타사", "경쟁사", "다른 곳"),
    "AD-URG-01": ("오늘만", "마감 임박", "선착순", "한정 수량", "지금 아니면", "마지막 기회"),
    "AD-ENDO-01": ("의사들이 추천", "전문가 인증", "임상 입증", "후기 10만", "만족도"),
    "AD-BRD-01": ("카지노", "도박", "토토"),
    "AD-FRD-01": ("선입금", "인증번호", "신용 무관", "무심사", "원격제어"),
}


def naive_blocklist() -> System:
    def run(creative: Creative) -> Prediction:
        t0 = time.perf_counter()
        findings: list[Finding] = []
        for field in AD_FIELDS:
            text = creative.text(field)
            low = text.lower()
            for rule_id, terms in BLOCKLIST.items():
                for term in terms:
                    i = low.find(term)
                    if i >= 0:
                        findings.append(
                            Finding(
                                rule_id=rule_id, code="blocklist", severity="reject", field=field,
                                quote=text[i:i + len(term)], start=i, end=i + len(term),
                            )
                        )
        outcome: Outcome = "REJECT" if findings else "APPROVE"
        return Prediction(outcome, findings, round((time.perf_counter() - t0) * 1000, 3))

    return run


def ablations() -> dict[str, System]:
    """전체 구성과, 구성요소를 하나씩 끈 구성."""
    base = ReviewConfig()
    out: dict[str, System] = {"full": pipeline_system(base)}
    for comp in ("normalize", "exceptions", "challenge", "landing"):
        out[f"-{comp}"] = pipeline_system(base.without(comp))
    return out
