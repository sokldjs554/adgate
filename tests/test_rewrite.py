from __future__ import annotations

from conftest import mk

from adgate.pipeline import review
from adgate.rewrite import rewrite

LOAN_OK = "과도한 대출은 개인신용평점 하락의 원인이 될 수 있습니다. 금리는 신용도에 따라 달라질 수 있습니다."
INV_OK = "투자 상품은 원금 손실이 발생할 수 있습니다."


def rw(c):
    return rewrite(c, review(c))


def test_superlatives_are_removed_and_the_fix_is_verified():
    c = mk("국내 1위 프라이팬, 최고의 요리를 경험하세요", "스크래치에 강한 코팅입니다.", category="ecommerce")
    r = rw(c)
    assert r.status == "verified"
    assert r.candidate is not None and r.candidate.headline == "프라이팬, 요리를 경험하세요"
    assert r.verification and r.verification.outcome_after == "APPROVE" and r.verification.retention >= 0.8


def test_guarantee_is_replaced_with_a_conditional_phrase():
    c = mk("무조건 승인, 오늘 바로 입금", "연 7.9% ~ 17.9% 모바일 신청", category="loan", disclosure=LOAN_OK)
    r = rw(c)
    assert r.status == "verified"
    assert r.candidate is not None
    assert "승인 여부는 심사 후 결정" in r.candidate.headline and "무조건" not in r.candidate.headline
    assert r.verification is not None and not r.verification.lost_claims and not r.verification.new_claims


def test_verb_ending_is_consumed_with_the_guarantee():
    c = mk("안정적인 자산 관리", "저희는 수익을 보장합니다. 지금 가입하세요.", category="invest", disclosure=INV_OK)
    r = rw(c)
    assert r.candidate is not None and "합니다" not in r.candidate.body.split(".")[0].replace("변동될 수 있음", "")
    assert r.status == "verified"


def test_missing_financial_disclosures_are_appended_to_the_disclosure_field():
    c = mk("직장인 신용대출 간편 신청", "모바일에서 신청하세요.", category="loan")
    r = rw(c)
    assert r.status == "verified"
    assert r.candidate is not None and r.candidate.disclosure is not None
    assert "개인신용평점" in r.candidate.disclosure and "금리는 신용도에 따라" in r.candidate.disclosure


def test_unfixable_violations_are_not_touched():
    c = mk("먹기만 하면 관절염 완치", "천연 추출물", category="supplement")
    r = rw(c)
    assert r.status == "unavailable" and r.candidate is None and r.applied == []
    assert "AD-HLT-01:disease_claim" in r.unresolved


def test_partial_when_only_some_violations_are_fixable():
    c = mk("국내 1위 영양제", "암 예방에 도움", category="supplement")
    r = rw(c)
    assert r.status == "partial"
    assert r.verification is not None and not r.verification.passes
    assert any(u.startswith("AD-HLT-01") for u in r.unresolved)


def test_sourced_superlative_is_left_for_a_human():
    c = mk("판매량 1위 (2026년 ○○리서치 조사 기준)", "생활가전", category="ecommerce")
    r = rw(c)
    assert r.status == "unavailable"


def test_comparison_urgency_and_endorsement_are_deleted():
    c = mk("오늘만 이 가격, 의사들이 추천하는 족욕기", "다른 곳보다 저렴하게 만나 보세요.", category="ecommerce")
    r = rw(c)
    assert r.status == "verified", r
    assert r.candidate is not None
    assert "오늘만" not in r.candidate.headline and "추천" not in r.candidate.headline and "다른 곳" not in r.candidate.body


def test_numbers_outside_the_violation_are_preserved():
    c = mk("국내 1위 정수기 렌탈 월 9,900원", "36개월 약정", category="ecommerce", landing="월 9,900원, 36개월 약정")
    r = rw(c)
    assert r.candidate is not None and "9,900원" in r.candidate.headline and "36개월" in r.candidate.body
    assert r.verification is not None and r.verification.lost_claims == []


def test_nothing_to_fix_for_an_approved_ad():
    c = mk("가계부 앱", "영수증을 찍으면 자동으로 분류돼요.", category="app")
    assert rw(c).status == "unavailable"


def test_headline_that_would_become_empty_is_not_offered():
    c = mk("국내 1위", "본문입니다.", category="ecommerce")
    r = rw(c)
    assert r.candidate is None and r.status == "unavailable"


def test_a_headline_reduced_to_a_word_is_not_verified():
    c = mk("임상 입증된 보습력", "피부 장벽을 채워 줍니다.", category="cosmetic")
    r = rw(c)
    assert r.candidate is not None and r.candidate.headline == "보습력"
    assert r.status == "partial" and r.verification is not None and r.verification.thin_headline


# --------------------------------------------------------------------------- 독립 리뷰 M8: '검증됨' 이 깨진 문장을 통과시키지 않는다
def _fix(headline, category="cosmetic", body="설명 문장입니다."):
    c = mk(headline, body, category=category, disclosure=INV_OK if category == "invest" else None)
    return rw(c)


def test_a_cut_through_the_middle_of_a_word_is_not_verified():
    r = _fix("유일하게 순한 성분 보습 크림")
    assert r.status == "partial" and r.verification and "mid_token_cut" in r.verification.problems
    r = _fix("의사 추천 5가지 성분 보습 크림 신제품")
    assert r.status == "partial" and r.verification and "mid_token_cut" in r.verification.problems


def test_unbalanced_brackets_and_quotes_are_not_verified():
    r = _fix("투자 안내 (수익보장) 서비스", "invest")
    assert r.status == "partial" and r.verification and "unbalanced_brackets" in r.verification.problems
    r = _fix('"무조건 승인" 대출 안내', "loan")
    assert r.status == "partial" and r.verification and "unbalanced_quotes" in r.verification.problems


def test_a_headline_with_no_original_word_left_is_not_verified():
    r = _fix("수익 보장", "invest")
    assert r.status == "partial" and r.verification and "headline_replaced" in r.verification.problems


def test_clean_fixes_still_pass_the_new_checks():
    r = _fix("국내 1위 보습 크림")
    assert r.status == "verified" and r.verification and r.verification.problems == []


def test_a_candidate_that_breaks_the_length_limit_is_unavailable_not_verified():
    long_disclosure = "가" * 1990  # 덧붙이는 금융 고지가 2000자를 넘긴다
    c = mk("무조건 승인 대출", "설명", category="loan", disclosure=long_disclosure)
    r = rw(c)
    assert r.status == "unavailable" and "candidate_invalid" in r.unresolved
