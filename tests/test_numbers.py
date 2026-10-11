"""광고-랜딩 대조의 금액 해석. 독립 작성자·라벨러가 지적한 표기('3만 9천 원', '천원')를 값으로 읽는다."""

from __future__ import annotations

import pytest
from conftest import mk

from adgate.config import ReviewConfig
from adgate.normalize import build
from adgate.numbers import extract, landing_values
from adgate.pipeline import review


def won(text: str) -> list[int]:
    return [c.value[0] for c in extract(build(text).soft) if c.kind == "won"]  # type: ignore[misc]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("월 9,900원", [9900]),
        ("월 49000원", [49000]),
        ("5만원", [50000]),
        ("5천만원", [50_000_000]),
        ("3만 9천 원", [39000]),               # 조각을 더한다(예전에는 마지막 조각 '9천 원' 만 읽었다)
        ("1억 5천만원", [150_000_000]),
        ("1천 5백원", [1500]),
        ("3.5만원", [35000]),
        ("천원", [1000]),                       # 숫자 없이 단위만 있는 금액
        ("첫 달 천원", [1000]),
        ("만원", [10000]),
        ("최대 만 원 할인", [10000]),
        ("월9,900원", [9900]),                  # 앞에 한글이 붙어도 숫자로 시작하면 읽는다
    ],
)
def test_amounts_are_read_as_values(text, expected):
    assert won(text) == expected


@pytest.mark.parametrize("text", ["수만 원 상당", "이만원", "원료를 받아요", "원금 보장", "만족합니다"])
def test_vague_or_unrelated_text_is_not_an_amount(text):
    assert won(text) == []


def test_the_quote_covers_the_whole_compound_amount():
    c = mk("월 3만 9천 원", "설명", landing="월 19,900원 / 연 49,000원")
    r = review(c, ReviewConfig(challenge=False))
    quotes = [f.quote for f in r.findings if f.rule_id == "AD-LND-01"]
    assert quotes == ["3만 9천 원"]  # '9천 원' 만이 아니라 전체


def test_unit_only_amount_is_compared_with_the_landing():
    mismatch = review(mk("첫 달 천원", "설명", landing="첫 달 1,500원"), ReviewConfig(challenge=False))
    assert [(f.rule_id, f.code) for f in mismatch.findings if f.rule_id == "AD-LND-01"] == [("AD-LND-01", "mismatch")]
    same = review(mk("첫 달 천원", "설명", landing="첫 달 1,000원"), ReviewConfig(challenge=False))
    assert not [f for f in same.findings if f.rule_id == "AD-LND-01"]


def test_landing_side_uses_the_same_reader():
    assert landing_values(build("3만 9천 원 또는 천원").soft)["won"] == {(39000,), (1000,)}
