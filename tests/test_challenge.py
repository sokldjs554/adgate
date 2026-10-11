from __future__ import annotations

from conftest import mk

from adgate.challenge import approx_ends, fuzzy_spans
from adgate.config import ReviewConfig
from adgate.pipeline import review


def loose(result):
    return [f for f in result.findings if f.code == "loose_match"]


def test_approx_ends_finds_one_edit_matches():
    assert any(d == 1 for _, d in approx_ends("abcd", "xxabxdxx", 1))
    assert not approx_ends("abcd", "xxxxxxxx", 1)


def test_fuzzy_catches_a_respelled_guarantee():
    spans = fuzzy_spans("수익보장", "안정적인수익보쟝으로시작")
    assert spans
    a, b = spans[0]
    assert "안정적인수익보쟝으로시작"[a:b] == "수익보쟝"


def test_fuzzy_ignores_exact_phrase_and_unrelated_text():
    assert fuzzy_spans("수익보장", "수익보장") == []
    assert fuzzy_spans("수익보장", "오늘의날씨는맑음") == []


def test_challenge_turns_a_respelled_guarantee_into_review():
    c = mk("안정적인 수익보쟝 상품", category="invest", disclosure="투자 상품은 원금 손실이 발생할 수 있습니다.")
    off = review(c, ReviewConfig(challenge=False))
    on = review(c)
    assert off.outcome == "APPROVE"
    assert on.outcome == "REVIEW"
    f = loose(on)[0]
    assert f.rule_id == "AD-GUA-01" and f.source == "challenge" and f.severity == "review"
    assert f.quote == "수익보쟝"


def test_challenge_catches_a_phrase_split_across_fields():
    c = mk("무조건", "승인 가능한 대출", category="loan",
           disclosure="과도한 대출은 개인신용평점 하락의 원인이 될 수 있습니다. 금리는 신용도에 따라 달라질 수 있습니다.")
    assert review(c, ReviewConfig(challenge=False)).outcome == "APPROVE"
    r = review(c)
    assert r.outcome == "REVIEW"
    assert any(f.note and f.note.startswith("cross_field") for f in loose(r))


def test_challenge_does_not_flag_negated_respelling():
    c = mk("수익보쟝은 하지 않습니다", category="invest", disclosure="투자 상품은 원금 손실이 발생할 수 있습니다.")
    assert not loose(review(c))


def test_challenge_skips_spots_the_strict_rule_already_judged():
    c = mk("무조건 승인", category="loan", disclosure="신용점수 하락 가능. 연 9.0% ~ 15.0%")
    r = review(c)
    assert r.outcome == "REJECT"
    assert not [f for f in loose(r) if f.rule_id == "AD-GUA-01"]


def test_sentence_boundary_stops_cross_field_matches():
    c = mk("무조건.", "승인 안내", category="app")
    assert not [f for f in loose(review(c)) if f.note and f.note.startswith("cross_field")]


def test_challenge_never_lowers_severity():
    for h in ("무조건 승인", "수익보장", "국내 1위 최고", "오늘만"):
        c = mk(h, category="invest", disclosure="투자 상품은 원금 손실이 발생할 수 있습니다.")
        a = review(c, ReviewConfig(challenge=False))
        b = review(c)
        rank = {"APPROVE": 0, "REVIEW": 1, "REJECT": 2}
        assert rank[b.outcome] >= rank[a.outcome]
