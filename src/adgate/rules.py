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
from .normalize import Mode, Normalized, build, suspicious_characters
from .policy import Policy

# 광고-랜딩 수치 대조는 '제안(offer)' 문구에만 한다. 고지 문구의 일반 수치는 제외한다.
LND_FIELDS = ("headline", "body", "cta", "image_text")
# 랜딩 페이지에서는 보장·질병 효능·제한 업종 표현만 검사하고, 찾아도 *사람 검토*(REVIEW)까지만 낸다. 랜딩은 광고 문구가 아니라 페이지 스냅샷이라
# 로그인 폼('비밀번호를 입력')·인증번호 입력·금연 안내 같은 정상 문구가 섞여 있다. 그래서 사기 유도(FRD)는 보지 않는다(독립 리뷰 M4).
LANDING_SCANNED = ("AD-GUA-01", "AD-HLT-01", "AD-BRD-01")


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
                # '환불 100% 보장' 은 거래 조건의 보장이다. 단 앞 낱말이 *바로* 거래 조건일 때, 그리고 '보장·보증' 꼴일 때만.
                if (
                    pat.name == "pct_guarantee" and m.group().endswith(("보장", "보증"))
                    and lx.TXN_BEFORE.search(nv.tight[max(0, m.start() - 10):m.start()])
                ):
                    continue
                if pat.txn_after and lx.TXN_AFTER.match(nv.tight[m.end():m.end() + 16]):
                    continue
            out.append(_hit(scan, "AD-GUA-01", "guarantee", field, nv, m.start(), m.end(), note=f"pattern:{pat.name}"))
    return out


def _is_range_expression(name: str, tight: str, end: int) -> bool:
    return name in lx.RANGE_PATS and lx.RANGE_AFTER.match(tight[end:end + 14]) is not None


def _has_source(text: str) -> bool:
    return bool(lx.SOURCE_MARK.search(text) and lx.YEAR_MARK.search(unicodedata.normalize("NFKC", text)))


def _substantiated(scan: Scan, nv: Normalized, a: int, b: int) -> bool:
    """근거(조사 주체 + 기준 연도)를 같은 문장에서, 아니면 같은 표지(*, ※ …)로 이어지는 각주에서 찾는다(POLICY: 같은 괄호·각주)."""
    sa, sb = nv.soft_span_from_tight(a, b)
    lo, hi = nv.sentence_range(sa)
    if _has_source(nv.soft[lo:hi]):
        return True
    _, end = nv.orig_span_from_tight(a, b)
    rest = nv.original[end:end + 3].lstrip()
    if rest and lx.FOOTNOTE_MARK.match(rest):
        marker = re.escape(rest[0])
        for field in AD_FIELDS:
            for m in re.finditer(rf"{marker}[^\n]{{0,120}}", scan.creative.text(field)):
                if _has_source(m.group()[1:]):
                    return True
    return False


def detect_abs(scan: Scan, field: str, nv: Normalized) -> list[Finding]:
    out: list[Finding] = []
    for pat in lx.ABS:
        for m in pat.rx.finditer(nv.tight):
            if scan.cfg.exceptions and _is_range_expression(pat.name, nv.tight, m.end()):
                continue
            ok = scan.cfg.exceptions and _substantiated(scan, nv, m.start(), m.end())
            code = "substantiated_needs_check" if ok else "unsubstantiated"
            out.append(_hit(scan, "AD-ABS-01", code, field, nv, m.start(), m.end(), note=f"pattern:{pat.name}"))
    return out


def detect_hlt(scan: Scan, field: str, nv: Normalized) -> list[Finding]:
    out: list[Finding] = []
    for pat in lx.HLT:
        for m in pat.rx.finditer(nv.soft):
            a, b = m.span()
            if scan.cfg.exceptions:
                _, hi = nv.sentence_range(a)
                stop = lx.HLT_CLAUSE_END.search(nv.soft, b, hi)
                tail = nv.soft[b:stop.start() if stop else hi]  # 질병 문구 뒤, 같은 절의 끝까지만 본다
                if lx.HLT_DISCLAIMER.search(tail) or lx.HLT_NEG_SOFT.match(tail):
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
    if digits:  # 수량은 '선착순·한정·재고' 같은 희소성 말과 이어진 숫자여야 한다('후기 100명' 은 근거가 아니다)
        n, unit, scarce = digits.group(), r"(?:명|개|세트|장|매|대|벌)", r"(?:선착순|한정|수량|재고|잔여|남은|마감)"
        return re.search(rf"{scarce}[^.!?\n]{{0,12}}{n}\s*{unit}|{n}\s*{unit}\s*{scarce}", landing.soft) is not None
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
                sentence = nv.soft[lo:hi]
                if lx.BRD_PUBLIC_INTEREST.search(sentence) and not lx.BRD_SALES.search(sentence):
                    code = "public_interest"
            out.append(_hit(scan, "AD-BRD-01", code, field, nv, m.start(), m.end(), note=f"pattern:{pat.name}"))
    return out


def detect_frd(scan: Scan, field: str, nv: Normalized) -> list[Finding]:
    out: list[Finding] = []
    for pat in lx.FRD:
        for m in pat.rx.finditer(nv.tight):
            if scan.cfg.exceptions and pat.negatable and (
                lx.negated(nv.tight, m.end(), without=True) or lx.negated_request(nv.tight, m.end())
            ):
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


def detect_hidden(scan: Scan) -> list[Finding]:
    """보이지 않는 채움 문자·글자 방향 제어 문자가 있는 소재는 자동 판정하지 않고 사람에게 넘긴다.
    정규화가 걷어 내도 *보이는 문구*와 *검사한 문구*가 어긋날 수 있는 입력이기 때문이다(방향을 뒤집으면 논리 순서가 거꾸로다)."""
    if not scan.cfg.normalize:
        return []
    where = [
        f"{field}:{'+'.join(kinds)}"
        for field in (*AD_FIELDS, "landing_text")
        if (text := scan.creative.text(field)) and (kinds := suspicious_characters(text))
    ]
    if not where:
        return []
    return [Finding(
        rule_id="SYS-INPUT", code="hidden_characters", severity=scan.policy.spec("SYS-INPUT", "hidden_characters").severity,
        note=", ".join(where),
    )]


def _has_warning(rx: re.Pattern[str], text: str) -> bool:
    """경고 구절이 있되, 바로 뒤에서 부정되지("손실 가능성이 없는", "하락 걱정 없이") 않은 것이 하나라도 있는가."""
    for m in rx.finditer(text):
        tail = re.split(r"[.!?\n]", text[m.end():m.end() + lx.WARNING_DENIAL_WINDOW])[0]
        if not lx.WARNING_DENIAL.search(tail):
            return True
    return False


def detect_category_mismatch(scan: Scan) -> list[Finding]:
    """선언한 업종과 문구가 맞지 않으면 사람에게 넘긴다. 업종(`advertiser.category`)은 광고주가 적는 값이라, 속이면 금융·건강 규칙이 통째로 꺼진다."""
    category = scan.category
    why: list[str] = []
    if category not in ("loan", "invest") and any(
        lx.FINANCE_CUE.search(nv.tight) for f, nv in scan.views.items() if f in AD_FIELDS
    ):
        why.append("finance_cue")
    if not scan.policy.applies("AD-HLT-01", category) and any(detect_hlt(scan, f, nv) for f, nv in scan.views.items() if f in AD_FIELDS):
        why.append("health_claim")
    if not why:
        return []
    return [Finding(
        rule_id="SYS-INPUT", code="category_mismatch", severity=scan.policy.spec("SYS-INPUT", "category_mismatch").severity,
        note=f"category={category}: {', '.join(why)}",
    )]


def detect_fin(scan: Scan) -> list[Finding]:
    category = scan.category
    if not scan.policy.applies("AD-FIN-01", category):
        return []
    text = "\n".join(scan.views[f].soft for f in AD_FIELDS if f in scan.views)
    missing: list[str] = []
    if category == "loan":
        if not _has_warning(lx.FIN_CREDIT_WARNING, text):
            missing.append("credit_warning")
        if not (lx.FIN_RATE_NUMERIC.search(text) or lx.FIN_RATE_PHRASE.search(text)):
            missing.append("rate_info")
    else:
        if not (_has_warning(lx.FIN_PRINCIPAL_LOSS, text) or lx.FIN_NOT_GUARANTEED.search(text)):
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
                for f in fn(scan, "landing_text", landing):
                    if f.severity == "reject":
                        f = f.model_copy(update={"severity": "review", "note": f"{f.note}; landing" if f.note else "landing"})
                    findings.append(f)
    findings.extend(detect_hidden(scan))
    findings.extend(detect_category_mismatch(scan))
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
