"""결정론적 규칙. 어휘 규칙, 부재(고지 누락), 메타데이터, 광고-랜딩 대조를 한곳에서 돌린다.

모든 발견은 원문 필드의 [start:end] 와 정확히 같은 인용을 갖는다(``Finding.quote == original[start:end]``).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from . import lexicon as lx
from . import numbers as nums
from .config import ReviewConfig
from .models import AD_FIELDS, Creative, Finding
from .normalize import Mode, Normalized, build
from .policy import Policy

# 광고-랜딩 수치 대조는 '제안(offer)' 문구에만 한다. 고지 문구의 일반 수치는 제외한다.
LND_FIELDS = ("headline", "body", "cta", "image_text")
# 랜딩 페이지에서는 하드 반려 계열만 검사한다.
LANDING_SCANNED = ("AD-GUA-01", "AD-HLT-01", "AD-FRD-01", "AD-BRD-01")


@dataclass
class Scan:
    creative: Creative
    cfg: ReviewConfig
    policy: Policy
    views: dict[str, Normalized]

    @property
    def category(self) -> str:
        return self.creative.advertiser.category


def build_scan(creative: Creative, cfg: ReviewConfig, policy: Policy) -> Scan:
    mode: Mode = "full" if cfg.normalize else "plain"
    views = {
        field: build(creative.text(field), mode)
        for field in (*AD_FIELDS, "landing_text")
        if creative.text(field)
    }
    return Scan(creative, cfg, policy, views)


def _hit(
    scan: Scan, rule_id: str, code: str, field: str, nv: Normalized, a: int, b: int, *,
    view: str = "tight", note: str | None = None,
) -> Finding:
    s, e = nv.orig_span_from_tight(a, b) if view == "tight" else nv.orig_span_from_soft(a, b)
    return Finding(
        rule_id=rule_id, code=code, severity=scan.policy.spec(rule_id, code).severity,
        field=field, quote=nv.original[s:e], start=s, end=e, note=note,
    )


def _meta(scan: Scan, rule_id: str, code: str, *, missing: list[str] | None = None) -> Finding:
    return Finding(
        rule_id=rule_id, code=code, severity=scan.policy.spec(rule_id, code).severity,
        field=None, quote=None, start=None, end=None, missing=missing,
    )


# ----------------------------------------------------------------------------- 어휘 규칙

def detect_gua(scan: Scan, field: str, nv: Normalized) -> list[Finding]:
    out: list[Finding] = []
    for pat in lx.GUA:
        for m in pat.rx.finditer(nv.tight):
            if scan.cfg.exceptions:
                if pat.negatable and lx.negated(nv.tight, m.end()):
                    continue
                if pat.name == "pct_guarantee" and lx.TXN_BEFORE.search(nv.tight[max(0, m.start() - 10):m.start()]):
                    continue
            out.append(_hit(scan, "AD-GUA-01", "guarantee", field, nv, m.start(), m.end(), note=f"pattern:{pat.name}"))
    return out


def _is_range_expression(name: str, tight: str, end: int) -> bool:
    return name in lx.RANGE_PATS and lx.RANGE_AFTER.match(tight[end:end + 3]) is not None


def _substantiated(nv: Normalized, a: int, b: int) -> bool:
    sa, sb = nv.soft_span_from_tight(a, b)
    lo, hi = nv.sentence_range(sa)
    sentence = nv.soft[lo:hi]
    return bool(lx.SOURCE_MARK.search(sentence) and lx.YEAR_MARK.search(sentence))


def detect_abs(scan: Scan, field: str, nv: Normalized) -> list[Finding]:
    out: list[Finding] = []
    for pat in lx.ABS:
        for m in pat.rx.finditer(nv.tight):
            if scan.cfg.exceptions and _is_range_expression(pat.name, nv.tight, m.end()):
                continue
            ok = scan.cfg.exceptions and _substantiated(nv, m.start(), m.end())
            code = "substantiated_needs_check" if ok else "unsubstantiated"
            out.append(_hit(scan, "AD-ABS-01", code, field, nv, m.start(), m.end(), note=f"pattern:{pat.name}"))
    return out


def detect_hlt(scan: Scan, field: str, nv: Normalized) -> list[Finding]:
    out: list[Finding] = []
    for pat in lx.HLT:
        for m in pat.rx.finditer(nv.soft):
            a, b = m.span()
            if scan.cfg.exceptions:
                lo, hi = nv.sentence_range(a)
                if lx.HLT_DISCLAIMER.search(nv.soft[lo:hi]) or lx.HLT_NEG_SOFT.match(nv.soft[b:hi]):
                    continue
            out.append(_hit(scan, "AD-HLT-01", "disease_claim", field, nv, a, b, view="soft", note=f"pattern:{pat.name}"))
    return out


def detect_cmp(scan: Scan, field: str, nv: Normalized) -> list[Finding]:
    out: list[Finding] = []
    for pat in lx.CMP_DISPARAGE:
        for m in pat.rx.finditer(nv.tight):
            out.append(_hit(scan, "AD-CMP-01", "disparagement", field, nv, m.start(), m.end(), note=f"pattern:{pat.name}"))
    compare = list(lx.CMP_COMPARE)
    for name in scan.cfg.competitors:
        tight_name = re.escape(unicodedata.normalize("NFKC", name).lower().replace(" ", ""))
        compare.append(lx.Pat("competitor", re.compile(rf"{tight_name}(?:보다|대비|와비교|과비교|에비해)")))
    for pat in compare:
        for m in pat.rx.finditer(nv.tight):
            out.append(_hit(scan, "AD-CMP-01", "comparison", field, nv, m.start(), m.end(), note=f"pattern:{pat.name}"))
    return out


def _urgency_corroborated(scan: Scan, matched: str) -> bool:
    landing = scan.views.get("landing_text")
    if landing is None or not scan.cfg.landing:
        return False
    if lx.URG_PERIOD.search(landing.soft):
        return True
    digits = re.search(r"\d+", matched)
    if digits:
        return re.search(rf"{digits.group()}\s*(?:명|개|세트|장|매|대|벌)", landing.soft) is not None
    return False


def detect_urg(scan: Scan, field: str, nv: Normalized) -> list[Finding]:
    out: list[Finding] = []
    for pat in lx.URG:
        for m in pat.rx.finditer(nv.tight):
            if scan.cfg.exceptions and _urgency_corroborated(scan, m.group()):
                continue
            out.append(_hit(scan, "AD-URG-01", "urgency_unverified", field, nv, m.start(), m.end(), note=f"pattern:{pat.name}"))
    return out


def detect_endo(scan: Scan, field: str, nv: Normalized) -> list[Finding]:
    out: list[Finding] = []
    for pat in lx.ENDO:
        for m in pat.rx.finditer(nv.tight):
            out.append(_hit(scan, "AD-ENDO-01", "unsubstantiated_endorsement", field, nv, m.start(), m.end(), note=f"pattern:{pat.name}"))
    return out


def detect_brd_text(scan: Scan, field: str, nv: Normalized) -> list[Finding]:
    out: list[Finding] = []
    for pat in lx.BRD:
        for m in pat.rx.finditer(nv.tight):
            code = "restricted_content"
            if scan.cfg.exceptions:
                sa, _ = nv.soft_span_from_tight(m.start(), m.end())
                lo, hi = nv.sentence_range(sa)
                if lx.BRD_PUBLIC_INTEREST.search(nv.soft[lo:hi]):
                    code = "public_interest"
            out.append(_hit(scan, "AD-BRD-01", code, field, nv, m.start(), m.end(), note=f"pattern:{pat.name}"))
    return out


def detect_frd(scan: Scan, field: str, nv: Normalized) -> list[Finding]:
    out: list[Finding] = []
    for pat in lx.FRD:
        for m in pat.rx.finditer(nv.tight):
            if scan.cfg.exceptions and pat.negatable and lx.negated(nv.tight, m.end()):
                continue
            out.append(_hit(scan, "AD-FRD-01", "deceptive_solicitation", field, nv, m.start(), m.end(), note=f"pattern:{pat.name}"))
    return out


# 규칙 ID → 탐지 함수. 업종 제한은 Policy.applies 로 건다.
TEXT_DETECTORS: tuple[tuple[str, Callable[[Scan, str, Normalized], list[Finding]]], ...] = (
    ("AD-GUA-01", detect_gua),
    ("AD-ABS-01", detect_abs),
    ("AD-HLT-01", detect_hlt),
    ("AD-CMP-01", detect_cmp),
    ("AD-URG-01", detect_urg),
    ("AD-ENDO-01", detect_endo),
    ("AD-BRD-01", detect_brd_text),
    ("AD-FRD-01", detect_frd),
)


# ----------------------------------------------------------------------------- 메타데이터·부재 규칙

def detect_meta(scan: Scan) -> list[Finding]:
    out: list[Finding] = []
    adv = scan.creative.advertiser
    if scan.policy.applies("AD-ADV-01", adv.category) and not adv.verified:
        out.append(_meta(scan, "AD-ADV-01", "advertiser_not_verified"))
    if adv.category in ("adult", "gambling"):
        out.append(_meta(scan, "AD-BRD-01", "restricted_category"))
    return out


def detect_fin(scan: Scan) -> list[Finding]:
    category = scan.category
    if not scan.policy.applies("AD-FIN-01", category):
        return []
    text = "\n".join(scan.views[f].soft for f in AD_FIELDS if f in scan.views)
    missing: list[str] = []
    if category == "loan":
        if not lx.FIN_CREDIT_WARNING.search(text):
            missing.append("credit_warning")
        if not (lx.FIN_RATE_NUMERIC.search(text) or lx.FIN_RATE_PHRASE.search(text)):
            missing.append("rate_info")
    else:
        if not lx.FIN_PRINCIPAL_LOSS.search(text):
            missing.append("principal_loss_warning")
        if lx.FIN_PAST_CITED.search(text) and not lx.FIN_PAST_NOTICE.search(text):
            missing.append("past_performance_notice")
    return [_meta(scan, "AD-FIN-01", "missing_disclosure", missing=missing)] if missing else []


def detect_landing(scan: Scan) -> list[Finding]:
    landing = scan.views.get("landing_text")
    if landing is None or not scan.cfg.landing:
        return []
    have = nums.landing_values(landing.soft)
    have_units = {v[1] for v in have["time"]}
    seen: set[tuple[str, tuple[object, ...]]] = set()
    out: list[Finding] = []
    for field in LND_FIELDS:
        nv = scan.views.get(field)
        if nv is None:
            continue
        for claim in nums.extract(nv.soft):
            key = (claim.kind, claim.value)
            if key in seen:
                continue
            seen.add(key)
            if claim.value in have[claim.kind]:
                continue
            if claim.kind == "time":
                same_unit = claim.value[1] in have_units
                code = "mismatch" if same_unit else "unverifiable"
            else:
                code = "mismatch" if have[claim.kind] else "unverifiable"
            out.append(_hit(scan, "AD-LND-01", code, field, nv, claim.start, claim.end, view="soft", note=f"claim:{claim.kind}"))
    return out


_EMOJI_RANGES = ((0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF))


def _emoji_count(text: str) -> int:
    return sum(
        1 for c in text
        if any(lo <= ord(c) <= hi for lo, hi in _EMOJI_RANGES) and unicodedata.category(c) in ("So", "Sk")
    )


def detect_format(scan: Scan) -> list[Finding]:
    """info 등급. 판정에 영향이 없다."""
    c = scan.creative
    out: list[Finding] = []
    if len(c.headline.strip()) > 30:
        out.append(_meta(scan, "AD-FMT-01", "headline_too_long"))
    if re.search(r"[!?]{2,}", " ".join(c.text(f) for f in AD_FIELDS)):
        out.append(_meta(scan, "AD-FMT-01", "repeated_punctuation"))
    if _emoji_count(" ".join(c.text(f) for f in AD_FIELDS)) >= 4:
        out.append(_meta(scan, "AD-FMT-01", "excessive_emoji"))
    if not c.cta.strip():
        out.append(_meta(scan, "AD-FMT-01", "missing_cta"))
    if c.headline.strip() and c.headline.strip() == c.body.strip():
        out.append(_meta(scan, "AD-FMT-01", "headline_equals_body"))
    return out


# ----------------------------------------------------------------------------- 실행과 정리

def run_rules(scan: Scan) -> list[Finding]:
    findings: list[Finding] = []
    for field in AD_FIELDS:
        nv = scan.views.get(field)
        if nv is None:
            continue
        for rule_id, fn in TEXT_DETECTORS:
            if scan.policy.applies(rule_id, scan.category):
                findings.extend(fn(scan, field, nv))
    landing = scan.views.get("landing_text")
    if landing is not None and scan.cfg.landing:
        for rule_id, fn in TEXT_DETECTORS:
            if rule_id in LANDING_SCANNED and scan.policy.applies(rule_id, scan.category):
                findings.extend(fn(scan, "landing_text", landing))
    findings.extend(detect_meta(scan))
    findings.extend(detect_fin(scan))
    findings.extend(detect_landing(scan))
    findings.extend(detect_format(scan))
    return findings


def resolve_overlaps(findings: list[Finding], policy: Policy) -> list[Finding]:
    """같은 필드에서 같은 표현을 가리키는 발견이 여럿이면 우선순위가 높은 규칙 하나만 남긴다(docs/POLICY.md 5절)."""
    spanned = [f for f in findings if f.start is not None and f.end is not None]
    others = [f for f in findings if f.start is None or f.end is None]
    spanned.sort(key=lambda f: (policy.rank(f.rule_id), -((f.end or 0) - (f.start or 0)), f.start or 0))
    kept: list[Finding] = []
    for f in spanned:
        assert f.start is not None and f.end is not None
        clash = any(
            k.field == f.field and k.start is not None and k.end is not None
            and f.start < k.end and k.start < f.end
            for k in kept
        )
        if not clash:
            kept.append(f)
    kept.sort(key=lambda f: (f.field or "", f.start or 0))
    return kept + others
