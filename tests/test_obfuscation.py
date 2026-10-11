"""위반 문구 × 우회 연산자: 어느 조합도 자동 승인되면 안 된다(최소 REVIEW).

독립 리뷰(docs/REVIEW.md C3)가 한글 채움 문자·점자 빈칸·글자마다 줄바꿈·괄호·방향 뒤집기로 같은 문구가 APPROVE 되는 것을 찾았다.
사례 몇 개를 막는 대신, 규칙마다 대표 문구를 모아 연산자 전부와 곱해 본다.
"""

from __future__ import annotations

import unicodedata

import pytest
from conftest import mk

from adgate.pipeline import review

RANK = {"APPROVE": 0, "REVIEW": 1, "REJECT": 2}
INV_OK = "투자 상품은 원금 손실이 발생할 수 있습니다."
LOAN_OK = "대출 이용 시 개인신용평점이 하락할 수 있습니다. 금리는 연 7.9%입니다."

# (규칙, 업종, 문구, 공백 없이 쓸 문구) — 한국어 문구
PHRASES = [
    ("GUA", "invest", "수익 보장"),
    ("GUA", "invest", "무조건 승인"),
    ("GUA", "invest", "원금 보장"),
    ("ABS", "general", "국내 1위"),
    ("ABS", "general", "최고의 품질"),
    ("HLT", "supplement", "당뇨 완치"),
    ("HLT", "supplement", "암 예방"),
    ("CMP", "general", "다른 곳은 바가지"),
    ("URG", "general", "오늘만"),
    ("URG", "general", "마감 임박"),
    ("ENDO", "general", "의사들이 추천"),
    ("ENDO", "general", "임상 입증"),
    ("BRD", "general", "카지노"),
    ("BRD", "general", "마약 판매"),
    ("FRD", "general", "선입금"),
    ("FRD", "general", "무심사 대출"),
]


def interleave(sep: str):
    def op(text: str) -> str:
        return sep.join(text.replace(" ", ""))
    return op


def brackets(text: str) -> str:
    chars = list(text.replace(" ", ""))
    out = []
    for i, ch in enumerate(chars):
        out.append(ch)
        if i < len(chars) - 1:
            out.append("(" if i % 2 == 0 else ")")
    return "".join(out)


def spaced(text: str) -> str:
    return " ".join(text.replace(" ", ""))


def double_spaced(text: str) -> str:
    return "  ".join(text.replace(" ", ""))


def nfd(text: str) -> str:
    return unicodedata.normalize("NFD", text)


def fullwidth(text: str) -> str:
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in text)


def rlo(text: str) -> str:
    return "‮" + text[::-1]  # 논리 순서는 거꾸로, 화면에는 원래 문구로 보인다


OPERATORS = {
    "공백": spaced,
    "공백 둘": double_spaced,
    "마침표": interleave("."),
    "하이픈": interleave("-"),
    "가운뎃점": interleave("·"),
    "별": interleave("★"),
    "이모지": interleave("🔥"),
    "제로폭": interleave("​"),
    "한글 채움": interleave("ㅤ"),
    "반각 채움": interleave("ﾠ"),
    "점자 빈칸": interleave("⠀"),
    "줄바꿈": interleave("\n"),
    "괄호": brackets,
    "NFD": nfd,
    "전각": fullwidth,
    "방향 뒤집기": rlo,
}


def _creative(category: str, text: str):
    disclosure = INV_OK if category == "invest" else (LOAN_OK if category == "loan" else None)
    return mk(text, "설명 문장입니다.", category=category, disclosure=disclosure)


@pytest.mark.parametrize("op_name", list(OPERATORS))
@pytest.mark.parametrize(("rule", "category", "phrase"), PHRASES, ids=[f"{r}:{p}" for r, _, p in PHRASES])
def test_obfuscated_violation_is_never_auto_approved(rule, category, phrase, op_name):
    plain = review(_creative(category, phrase))
    assert RANK[plain.outcome] >= 1, f"기준선부터 놓침: {phrase}"
    obfuscated = OPERATORS[op_name](phrase)
    got = review(_creative(category, obfuscated))
    assert RANK[got.outcome] >= 1, f"{rule} {phrase!r} × {op_name} = {obfuscated!r} → {got.outcome}"


@pytest.mark.parametrize("op_name", ["공백", "마침표", "별", "제로폭", "한글 채움", "점자 빈칸", "괄호", "NFD"])
def test_obfuscated_violations_keep_the_exact_original_quote(op_name):
    c = _creative("invest", OPERATORS[op_name]("수익 보장"))
    for f in review(c).findings:
        if f.quote is not None:
            assert c.text(f.field)[f.start:f.end] == f.quote  # type: ignore[index]


@pytest.mark.parametrize("op_name", ["한글 채움", "반각 채움", "점자 빈칸", "방향 뒤집기"])
def test_hidden_characters_are_flagged_for_a_person_even_when_nothing_else_fires(op_name):
    """보이지 않는 문자만 든 깨끗한 소재도 사람에게 넘긴다(검사한 문구와 화면의 문구가 다를 수 있다)."""
    r = review(mk(OPERATORS[op_name]("좋은 하루"), "설명 문장입니다."))
    assert r.outcome == "REVIEW" and [f.rule_id for f in r.findings if f.severity == "review"] == ["SYS-INPUT"]


def test_ordinary_invisible_characters_in_emoji_do_not_trigger_the_escalation():
    # ZWJ(U+200D) 로 이어 붙인 가족 이모지, 변형 선택자
    r = review(mk("가족 여행 👨‍👩‍👧 ❤️", "설명 문장입니다."))
    assert r.outcome == "APPROVE"
