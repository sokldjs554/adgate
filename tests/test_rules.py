"""규칙별 '예시가 곧 명세' 테스트. docs/POLICY.md 의 위반 예와 '위반이 아닌 예'를 그대로 옮겼다."""

from __future__ import annotations

import pytest
from conftest import codes, mk

from adgate.config import ReviewConfig

GUA = ("AD-GUA-01", "guarantee")
INV_OK = "투자 상품은 원금 손실이 발생할 수 있습니다."
LOAN_OK = "과도한 대출은 개인신용평점 하락의 원인이 될 수 있습니다. 금리는 신용도에 따라 달라질 수 있습니다."


# --------------------------------------------------------------------------- AD-GUA-01
@pytest.mark.parametrize(
    "text",
    [
        "무조건 승인", "100% 승인", "수익 보장", "원금 보장", "확정 수익", "손실 없는 투자", "한 달 만에 10kg 감량",
        "3개월 만에 2배 수익", "수 익 보 장", "수.익.보.장", "무​조​건 승​인", "무조껀 승인",
        "100% 합격 보장", "수익을 보장합니다", "원금이 보장됩니다",
    ],
)
def test_guarantee_is_flagged(text):
    assert GUA in codes(mk(text, category="invest", disclosure=INV_OK))


@pytest.mark.parametrize(
    "text",
    [
        "수익을 보장하지 않습니다", "원금이 보장되지 않습니다", "원금 비보장 상품", "100% 환불 보장", "교환·환불 100% 보장",
        "품질 보증", "수익 보장 없음", "수익을 보장하지는 않으며 손실이 발생할 수 있습니다", "교환 보장",
        "무조건 환불은 어렵습니다",
    ],
)
def test_guarantee_lookalikes_are_not_flagged(text):
    assert GUA not in codes(mk(text, category="invest", disclosure=INV_OK))


def test_exceptions_off_flags_negated_guarantee():
    cfg = ReviewConfig(challenge=False, exceptions=False)
    assert GUA in codes(mk("수익을 보장하지 않습니다", category="invest", disclosure=INV_OK), cfg)


def test_disclosure_does_not_cure_a_guarantee():
    assert GUA in codes(mk("원금 보장형 투자", category="invest", disclosure=INV_OK))


# --------------------------------------------------------------------------- AD-ABS-01
@pytest.mark.parametrize(
    "text", ["최고의 품질", "국내 1위", "업계 최초", "유일한 선택", "완벽한 보호", "100% 만족", "No.1 앱", "No. 1 앱", "N o . 1", "최고급 원단"],
)
def test_superlative_is_rejected(text):
    assert ("AD-ABS-01", "unsubstantiated") in codes(mk(text))


def test_sourced_superlative_goes_to_review():
    c = mk("판매량 1위 (2026년 ○○리서치 조사 기준)")
    assert ("AD-ABS-01", "substantiated_needs_check") in codes(c)
    assert ("AD-ABS-01", "unsubstantiated") not in codes(c)


def test_year_alone_is_not_a_source():
    assert ("AD-ABS-01", "unsubstantiated") in codes(mk("2026년 기준 국내 1위"))


@pytest.mark.parametrize(
    "text",
    ["최고 연 5.9%", "최고 50% 할인", "최저 월 9,900원", "최고 한도 5천만원", "최초 가입 시 혜택", "최고 36개월 무이자", "최저 금리 안내"],
)
def test_range_expressions_are_not_superlatives(text):
    assert not any(r == "AD-ABS-01" for r, _ in codes(mk(text, landing="연 5.9% 50% 9,900원 5천만원 36개월")))


# --------------------------------------------------------------------------- AD-HLT-01
@pytest.mark.parametrize(
    "text",
    ["관절염 완치", "암 예방", "당뇨 개선", "아토피 치료", "여드름 흉터 치료", "위암 예방", "탈모증 치료", "당뇨를 치료합니다"],
)
def test_disease_claims_are_rejected(text):
    assert ("AD-HLT-01", "disease_claim") in codes(mk(text, category="supplement"))


@pytest.mark.parametrize(
    "text",
    [
        "본 제품은 질병의 예방 및 치료를 위한 의약품이 아닙니다.", "당뇨 환자는 섭취 전 의사와 상담하세요.", "피부 주름 개선에 도움",
        "혈행 개선에 도움을 줄 수 있습니다", "암호를 설정하세요", "함께 암기해요", "당뇨 예방 및 치료를 위한 의약품이 아닙니다.",
        "탈모 증상 완화에 도움", "자궁암 검진 안내",
    ],
)
def test_health_lookalikes_are_not_flagged(text):
    assert not any(r == "AD-HLT-01" for r, _ in codes(mk(text, category="supplement")))


def test_hlt_is_limited_to_product_categories():
    assert not any(r == "AD-HLT-01" for r, _ in codes(mk("암 예방", category="app")))


# --------------------------------------------------------------------------- AD-CMP-01
def test_comparison_is_review_and_disparagement_is_reject():
    assert ("AD-CMP-01", "comparison") in codes(mk("타사 대비 2배 빠름"))
    assert ("AD-CMP-01", "comparison") in codes(mk("다른 곳보다 저렴해요"))
    assert ("AD-CMP-01", "disparagement") in codes(mk("다른 곳은 바가지"))
    assert ("AD-CMP-01", "disparagement") in codes(mk("타사는 사기"))


@pytest.mark.parametrize("text", ["다른 사람과 함께 달려요", "견적을 한 번에 비교해 드립니다", "경쟁이 치열한 시장"])
def test_comparison_lookalikes_are_not_flagged(text):
    assert not any(r == "AD-CMP-01" for r, _ in codes(mk(text)))


def test_named_competitor_comparison():
    cfg = ReviewConfig(challenge=False, competitors=("포도뱅크",))
    assert ("AD-CMP-01", "comparison") in codes(mk("포도뱅크보다 수수료가 낮아요"), cfg)


# --------------------------------------------------------------------------- AD-URG-01
@pytest.mark.parametrize("text", ["오늘만 이 가격", "마감 임박", "선착순 100명", "한정 수량", "지금 아니면 못 사요", "마지막 기회"])
def test_urgency_without_evidence_goes_to_review(text):
    assert ("AD-URG-01", "urgency_unverified") in codes(mk(text))


def test_urgency_with_dated_landing_is_corroborated():
    c = mk("마감 임박", landing="행사 기간: 10월 1일 ~ 10월 31일")
    assert ("AD-URG-01", "urgency_unverified") not in codes(c)


def test_urgency_with_unrelated_landing_is_still_flagged():
    assert ("AD-URG-01", "urgency_unverified") in codes(mk("마감 임박", landing="신상품 입고 기념 이벤트"))


def test_rate_range_in_landing_is_not_period_evidence():
    assert ("AD-URG-01", "urgency_unverified") in codes(mk("오늘만", landing="금리 연 6.5% ~ 15.9%"))


def test_landing_off_removes_corroboration():
    c = mk("마감 임박", landing="행사 기간: 10월 1일 ~ 10월 31일")
    assert ("AD-URG-01", "urgency_unverified") in codes(c, ReviewConfig(challenge=False, landing=False))


# --------------------------------------------------------------------------- AD-ENDO-01
@pytest.mark.parametrize(
    "text",
    ["의사들이 추천하는", "전문가 인증", "연예인 ○○ 사용", "배우 ○○○가 쓰는", "임상 입증된", "후기 10만 건", "만족도 98%", "의사 추천"],
)
def test_endorsement_goes_to_review(text):
    assert ("AD-ENDO-01", "unsubstantiated_endorsement") in codes(mk(text))


@pytest.mark.parametrize("text", ["전문가용 드릴", "후기 확인하기", "의사소통이 쉬운 앱", "교수법 강의"])
def test_endorsement_lookalikes_are_not_flagged(text):
    assert not any(r == "AD-ENDO-01" for r, _ in codes(mk(text)))


# --------------------------------------------------------------------------- AD-LND-01
def test_landing_mismatch_unverifiable_and_match():
    ad = "월 9,900원 구독"
    assert ("AD-LND-01", "mismatch") in codes(mk(ad, landing="월 19,900원"))
    assert ("AD-LND-01", "unverifiable") in codes(mk(ad, landing="가격은 상담 후 안내"))
    assert not any(r == "AD-LND-01" for r, _ in codes(mk(ad, landing="월 9,900원, 월 19,900원")))
    assert not any(r == "AD-LND-01" for r, _ in codes(mk(ad)))  # 랜딩이 없으면 적용하지 않는다


def test_money_units_are_normalized():
    assert not any(r == "AD-LND-01" for r, _ in codes(mk("월 5만원", landing="월 50,000원")))
    assert not any(r == "AD-LND-01" for r, _ in codes(mk("한도 5천만원", landing="최대 50,000,000원")))


def test_dates_are_not_period_claims():
    assert not any(r == "AD-LND-01" for r, _ in codes(mk("10월 31일까지", landing="가격 안내")))


def test_time_mismatch_requires_same_unit():
    assert ("AD-LND-01", "unverifiable") in codes(mk("7일 무료", landing="환불은 3개월 이내"))
    assert ("AD-LND-01", "mismatch") in codes(mk("7일 무료", landing="환불은 30일 이내"))


# --------------------------------------------------------------------------- AD-FIN-01
def _fin(c):
    from adgate.pipeline import review

    r = review(c, ReviewConfig(challenge=False))
    return [f for f in r.findings if f.rule_id == "AD-FIN-01"]


def test_loan_missing_disclosures():
    f = _fin(mk("직장인 대출", category="loan"))
    assert len(f) == 1 and f[0].missing == ["credit_warning", "rate_info"]
    assert not _fin(mk("직장인 대출", disclosure=LOAN_OK, category="loan"))


def test_loan_numeric_rate_counts_as_rate_info():
    f = _fin(mk("연 7.9% ~ 17.9%", disclosure="대출 이용 시 개인신용평점이 하락할 수 있습니다.", category="loan"))
    assert not f


def test_invest_principal_loss_and_past_performance():
    f = _fin(mk("지난 1년 수익률 12.3% 기록", disclosure=INV_OK, category="invest"))
    assert f and f[0].missing == ["past_performance_notice"]
    ok = "원금 비보장 상품입니다. 과거 수익률은 미래 수익률을 보장하지 않습니다."
    assert not _fin(mk("지난 1년 수익률 12.3% 기록", disclosure=ok, category="invest"))
    assert _fin(mk("분산 투자", category="invest"))[0].missing == ["principal_loss_warning"]


def test_fin_does_not_apply_to_other_categories():
    assert not _fin(mk("분산 투자", category="app"))


# --------------------------------------------------------------------------- AD-BRD / FRD / ADV
def test_restricted_category_and_content():
    assert ("AD-BRD-01", "restricted_category") in codes(mk("경기 예측", category="gambling"))
    assert ("AD-BRD-01", "restricted_content") in codes(mk("해외 카지노 사이트 가입"))
    assert ("AD-BRD-01", "public_interest") in codes(mk("도박 중독 상담 센터"))
    assert ("AD-BRD-01", "public_interest") in codes(mk("마약 중독 치료·재활 상담"))


@pytest.mark.parametrize("text", ["성인 금연 캠페인", "토토로 굿즈 판매", "혐오감 없는 방충제"])
def test_brd_lookalikes_are_not_flagged(text):
    assert not any(r == "AD-BRD-01" for r, _ in codes(mk(text)))


@pytest.mark.parametrize(
    "text", ["선입금 후 대출 실행", "인증번호를 알려주세요", "신용 무관 대출", "원격제어 앱 설치 후 상담", "보증료 선납 시 입금", "무심사 대출"],
)
def test_fraud_solicitation_is_rejected(text):
    assert ("AD-FRD-01", "deceptive_solicitation") in codes(mk(text))


@pytest.mark.parametrize(
    "text", ["인증번호는 절대 타인에게 알려주지 마세요", "카드번호 입력 없이 결제", "비밀번호를 묻지 않습니다. 알려주지 마세요"],
)
def test_fraud_warnings_are_not_flagged(text):
    assert not any(r == "AD-FRD-01" for r, _ in codes(mk(text)))


def test_unverified_financial_advertiser_is_rejected():
    assert ("AD-ADV-01", "advertiser_not_verified") in codes(mk("대출", category="loan", verified=False))
    assert ("AD-ADV-01", "advertiser_not_verified") in codes(mk("투자", category="invest", verified=False))
    assert not any(r == "AD-ADV-01" for r, _ in codes(mk("앱", category="app", verified=False)))


# --------------------------------------------------------------------------- 필드·겹침
def test_violation_in_each_ad_field_is_found_with_exact_quote():
    from adgate.pipeline import review

    for field in ("headline", "body", "cta", "disclosure", "image_text"):
        kwargs = {field: "무조건 승인"}
        r = review(mk(**kwargs), ReviewConfig(challenge=False))
        hits = [f for f in r.findings if f.rule_id == "AD-GUA-01"]
        assert hits and hits[0].field == field and hits[0].quote == "무조건 승인"


def test_the_landing_is_scanned_for_guarantees_health_claims_and_restricted_topics_but_only_a_person_decides():
    from adgate.config import ReviewConfig
    from adgate.pipeline import review

    c = mk("영양제", category="supplement", landing="이 제품은 당뇨를 치료합니다")
    assert ("AD-HLT-01", "disease_claim") in codes(c)
    r = review(c, ReviewConfig(challenge=False))
    assert r.outcome == "REVIEW" and all(f.severity == "review" for f in r.findings if f.field == "landing_text")
    assert not any(r == "AD-ABS-01" for r, _ in codes(mk("영양제", landing="국내 1위 제품")))


@pytest.mark.parametrize(
    "landing",
    ["로그인\n아이디와 비밀번호를 입력하세요.\n회원가입", "가입 시 받은 인증번호를 입력해 주세요", "선입금 없이 바로 시작하세요"],
)
def test_ordinary_landing_page_forms_are_not_fraud(landing):
    assert not any(r == "AD-FRD-01" for r, _ in codes(mk("가계부 앱", landing=landing)))


def test_overlap_keeps_the_most_specific_rule():
    # "100% 승인": GUA 하나만. ABS 의 '100% 만족…' 패턴과 겹치지 않는다.
    found = codes(mk("100% 승인", category="loan", disclosure=LOAN_OK))
    assert ("AD-GUA-01", "guarantee") in found
    assert not any(r == "AD-ABS-01" for r, _ in found)


def test_clean_ad_is_approved():
    from adgate.pipeline import review

    r = review(mk("가계부 앱", "영수증을 찍으면 자동으로 분류돼요.", category="app"))
    assert r.outcome == "APPROVE" and not r.reasons


# --------------------------------------------------------------------------- 회귀: T-048 (부정 표현은 서술어 부정이어야 한다)
def test_distant_without_is_not_a_negation():
    # '걱정 없이'는 부사다. 앞의 '수익 보장'을 부정하지 않는다.
    assert GUA in codes(mk("해외 주식, 수 익 보 장 으로 걱정 없이 시작하세요", category="invest", disclosure=INV_OK))
    assert GUA in codes(mk("수익 보장 걱정없이 시작", category="invest", disclosure=INV_OK))


def test_adjacent_predicate_negation_still_works():
    for text in ("원금 보장 상품이 아닙니다", "원금 보장은 되지 않습니다", "수익 보장 불가", "수익을 보장하지 않습니다"):
        assert GUA not in codes(mk(text, category="invest", disclosure=INV_OK)), text


def test_without_is_a_negation_only_for_credentials():
    assert not any(r == "AD-FRD-01" for r, _ in codes(mk("카드번호 입력 없이 결제")))
    assert ("AD-FRD-01", "deceptive_solicitation") in codes(mk("인증번호를 알려주세요, 걱정 없이"))
