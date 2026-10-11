from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from adgate.normalize import build

ALPHABET = list("수익보장무조건승인 .-_*​‌１２３００％%abNo!?\n가나다선입保障с★🔥")  # 분해형 자모·한자·키릴·장식 기호 포함


def test_quote_roundtrip_with_zero_width():
    n = build("무​조​건 승​인")
    assert n.tight == "무조건승인"
    s, e = n.orig_span_from_tight(0, len(n.tight))
    assert n.original[s:e] == "무​조​건 승​인"


def test_fullwidth_digits_and_percent_become_ascii():
    n = build("１００％ 만족")
    assert n.tight == "100%만족"
    s, e = n.orig_span_from_tight(0, 5)
    assert n.original[s:e] == "１００％ 만"


def test_separators_between_syllables_are_removed():
    assert build("수.익.보.장").tight == "수익보장"
    assert build("수-익_보*장").tight == "수익보장"
    assert build("수 익  보 장").tight == "수익보장"


def test_numeric_punctuation_is_preserved():
    assert build("연 3.5% 2026-10-31 9~18%").tight == "연3.5%2026-10-319~18%"


def test_sentence_end_punctuation_survives_in_tight():
    n = build("보장. 수익")
    assert "." in n.tight


def test_sentence_range():
    n = build("첫 문장입니다. 두 번째 문장! 세 번째")
    i = n.soft.index("두 번째")
    lo, hi = n.sentence_range(i)
    assert n.soft[lo:hi].strip() == "두 번째 문장!"


def test_plain_mode_keeps_exotic_evasions():
    assert build("무​조건", "plain").tight != "무조건"
    assert build("무 조 건", "plain").tight == "무조건"  # 일반 공백만 제거


@given(st.text(alphabet=ALPHABET, max_size=60))
def test_spans_stay_inside_the_original(text):
    n = build(text)
    for a in range(len(n.tight)):
        for b in (a + 1, min(len(n.tight), a + 4)):
            if b <= a:
                continue
            s, e = n.orig_span_from_tight(a, b)
            assert 0 <= s < e <= len(text)


def test_separator_surrounded_by_spaces_is_removed():
    assert build("N o . 1").tight == "no1"
    assert build("수 - 익 · 보 장").tight == "수익보장"


def test_sentence_period_followed_by_space_is_a_boundary_not_a_separator():
    assert build("수익. 보장").tight == "수익.보장"
    assert build("서울 - 부산").tight == "서울부산"  # 공백 낀 하이픈은 단어를 이어 붙여도 해가 없다


def test_digit_ranges_with_spaces_survive():
    assert build("9 ~ 18%").tight == "9~18%"
