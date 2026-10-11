"""도전자(challenger): 엄격한 규칙이 놓쳤을 수 있는 변형 표기를 느슨하게 한 번 더 찾는다.

* 변형 표기: 위반 핵심 구절과 *자모 하나* 차이나는 표현("수익보쟝", "무조껀승인"처럼 철자를 비튼 경우).
* 필드를 걸친 표현: 제목의 끝과 본문의 시작에 나눠 쓴 구절("무조건" / "승인").

여기서 찾은 것은 항상 ``review`` 등급이다. 즉 도전자는 판정을 더 보수적으로만 만든다(APPROVE → REVIEW).
엄격한 규칙이 이미 같은 자리를 판정했거나, 구절이 정확히 같은 표현(거리 0)이면 건드리지 않는다.
"""

from __future__ import annotations

import unicodedata

from . import lexicon as lx
from .models import Finding
from .normalize import Mode, Normalized, build
from .rules import TEXT_DETECTORS, Scan

# (규칙, tight 형태의 핵심 구절, 부정문 예외를 적용할지). 정확히 같으면 위반인 구절만 둔다.
FUZZY_PHRASES: tuple[tuple[str, str, bool], ...] = (
    ("AD-GUA-01", "무조건승인", True),
    ("AD-GUA-01", "수익보장", True),
    ("AD-GUA-01", "원금보장", True),
    ("AD-GUA-01", "확정수익", True),
    ("AD-GUA-01", "수익확정", True),
    ("AD-GUA-01", "손실없는", False),
    ("AD-GUA-01", "100%승인", True),
    ("AD-FRD-01", "신용무관", False),
    ("AD-FRD-01", "무심사대출", False),
    ("AD-FRD-01", "선수수료", False),
    ("AD-ABS-01", "업계최초", False),
    ("AD-ABS-01", "업계유일", False),
    ("AD-ABS-01", "국내1위", False),
    ("AD-URG-01", "마감임박", False),
    ("AD-ENDO-01", "의사추천", False),
    ("AD-ENDO-01", "전문가추천", False),
    ("AD-ENDO-01", "임상입증", False),
)
# 자모 하나 차이 나는 *정상어*. 도전자가 보류하지 않는다(예: 수익보상 ≠ 수익보장).
FUZZY_BENIGN = frozenset({"수익보상", "원금보상", "손실보상", "수익분장"})

# 표기 변형을 접는다: 쌍자음→평음(껀/건, 깐/간), ㅘ→ㅏ, ㅝ→ㅓ, ㅙ·ㅞ·ㅐ→ㅔ, 쌍자음 받침→평음.
_FOLD = {
    0x1101: 0x1100, 0x1104: 0x1103, 0x1108: 0x1107, 0x110A: 0x1109, 0x110D: 0x110C,
    0x116A: 0x1161, 0x116F: 0x1165, 0x116B: 0x1166, 0x1170: 0x1166, 0x1162: 0x1166,
    0x11A9: 0x11A8, 0x11BB: 0x11BA,
}
CROSS_FIELD_PAIRS = (("headline", "body"), ("body", "cta"))
CHALLENGE_FIELDS = ("headline", "body", "cta", "image_text", "disclosure")


def fold(text: str) -> str:
    """철자 변형을 접은 문자열. 글자 수는 그대로다(한 글자 → 한 글자)."""
    out: list[str] = []
    for ch in text:
        folded = unicodedata.normalize("NFC", unicodedata.normalize("NFD", ch).translate(_FOLD))
        out.append(folded if len(folded) == 1 else ch)
    return "".join(out)


def _decompose(tight: str) -> tuple[str, list[int]]:
    jamo: list[str] = []
    owner: list[int] = []
    for i, ch in enumerate(tight):
        for j in unicodedata.normalize("NFD", ch):
            jamo.append(j)
            owner.append(i)
    return "".join(jamo), owner


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


def approx_ends(pattern: str, text: str, k: int = 1) -> list[tuple[int, int]]:
    """text 안에서 pattern 과 편집 거리 ≤ k 인 부분 문자열의 (끝 위치(배타), 거리) 목록 (Sellers)."""
    m = len(pattern)
    prev = list(range(m + 1))
    out: list[tuple[int, int]] = []
    for j, ch in enumerate(text, start=1):
        cur = [0] * (m + 1)
        for i in range(1, m + 1):
            cur[i] = min(prev[i] + 1, cur[i - 1] + 1, prev[i - 1] + (pattern[i - 1] != ch))
        if cur[m] <= k:
            out.append((j, cur[m]))
        prev = cur
    return out


def _may_match(phrase: str, tight: str) -> bool:
    """자모 하나를 고쳐도 음절 두 글자 묶음 중 적어도 하나는 그대로 남는다. 하나도 없으면 DP를 돌릴 필요가 없다."""
    return any(phrase[i:i + 2] in tight for i in range(len(phrase) - 1))


def fuzzy_spans(phrase: str, tight: str) -> list[tuple[int, int]]:
    """phrase 와 (표기를 접은 뒤) 자모 하나 이하로 다른 구간의 tight 인덱스 [a, b) 목록.

    원문에 phrase 가 정확히 있는 자리와 그 곁은 제외한다(엄격한 규칙의 몫). 정상어 stoplist 도 제외한다.
    """
    f_phrase, f_tight = fold(phrase), fold(tight)
    if not _may_match(f_phrase, f_tight):
        return []
    jp_exact, _ = _decompose(phrase)
    jt_exact, _ = _decompose(tight)
    exact = {j for j, d in approx_ends(jp_exact, jt_exact, 0)}
    jp, _ = _decompose(f_phrase)
    jt, owner = _decompose(f_tight)
    spans: list[tuple[int, int]] = []
    last_end = -1
    for j, _d in approx_ends(jp, jt, 1):
        if {j - 1, j, j + 1} & exact:
            continue
        best: tuple[int, int] | None = None
        for length in (len(jp), len(jp) - 1, len(jp) + 1):
            start = j - length
            if start >= 0 and _edit_distance(jp, jt[start:j]) <= 1:
                best = (start, j)
                break
        if best is None or best[0] < last_end:
            continue
        a, b = owner[best[0]], owner[best[1] - 1] + 1
        if tight[a:b] in FUZZY_BENIGN:
            continue
        last_end = best[1]
        spans.append((a, b))
    return spans


def _overlaps_strict(strict: list[Finding], rule_id: str, field: str, s: int, e: int) -> bool:
    return any(
        f.field == field and f.rule_id == rule_id and f.start is not None and f.end is not None
        and s < f.end and f.start < e
        for f in strict
    )


def _loose(scan: Scan, rule_id: str, field: str | None, nv: Normalized | None, span: tuple[int, int] | None,
           note: str) -> Finding:
    quote = start = end = None
    if nv is not None and span is not None:
        start, end = span
        quote = nv.original[start:end]
    return Finding(
        rule_id=rule_id, code="loose_match", severity=scan.policy.spec(rule_id, "loose_match").severity,
        field=field, quote=quote, start=start, end=end, source="challenge", note=note,
    )


def challenge(scan: Scan, strict: list[Finding]) -> list[Finding]:
    out: list[Finding] = []

    # 1) 변형 표기
    for field in CHALLENGE_FIELDS:
        nv = scan.views.get(field)
        if nv is None:
            continue
        for rule_id, phrase, negatable in FUZZY_PHRASES:
            if not scan.policy.applies(rule_id, scan.category):
                continue
            for a, b in fuzzy_spans(phrase, nv.tight):
                if negatable and scan.cfg.exceptions and lx.negated(nv.tight, b):
                    continue
                s, e = nv.orig_span_from_tight(a, b)
                if _overlaps_strict(strict, rule_id, field, s, e):
                    continue
                out.append(_loose(scan, rule_id, field, nv, (s, e), f"fuzzy:{phrase}"))

    # 2) 필드에 걸친 표현: 인접한 두 필드를 이어 붙여 엄격한 규칙을 다시 돌리고, 경계를 가로지르는 것만 남긴다.
    for left, right in CROSS_FIELD_PAIRS:
        a_text, b_text = scan.creative.text(left), scan.creative.text(right)
        if not a_text or not b_text:
            continue
        joined_text = f"{a_text} {b_text}"
        mode: Mode = "full" if scan.cfg.normalize else "plain"
        joined = build(joined_text, mode)
        boundary = len(a_text)
        for rule_id, fn in TEXT_DETECTORS:
            if not scan.policy.applies(rule_id, scan.category):
                continue
            for f in fn(scan, f"{left}+{right}", joined):
                if f.start is None or f.end is None or not (f.start <= boundary < f.end):
                    continue
                out.append(_loose(scan, f.rule_id, None, None, None, f"cross_field:{left}+{right}:{f.quote}"))
    return out
