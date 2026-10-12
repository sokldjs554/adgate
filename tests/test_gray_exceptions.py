"""회색지대(gray) 첫 접촉에서 정상 소재를 반려한 원인 세 가지에 대한 좁은 예외와 그 *악용* 시험(docs/DECISIONS.md D-12, docs/FAILURES.md).

예외를 넓히거나 좁힐 때마다 악용 사례(예외를 흉내 내는 위반)와 정상 유사 사례를 함께 시험한다.
"""

from __future__ import annotations

import pytest

from adgate.config import ReviewConfig
from adgate.models import Creative
from adgate.pipeline import review


def run(*, headline: str = "안내", body: str = "안내", name: str = "가상회사", category: str = "general", image_text: str | None = None,
        disclosure: str | None = None):
    c = Creative.model_validate({
        "ad_id": "x", "advertiser": {"name": name, "category": category, "verified": True}, "headline": headline, "body": body, "cta": "자세히 보기",
        "image_text": image_text, "disclosure": disclosure,
    })
    r = review(c, ReviewConfig())
    return r.outcome, [(f.rule_id, f.quote) for f in r.findings if f.quote]


# --------------------------------------------------------------------------- 1. '~하는 N이 아니라/아니므로' 꼴의 부정
@pytest.mark.parametrize("body", [
    "수익을 약속하는 강의가 아니라 판단 기준을 세우는 강의입니다.",
    "이 과정은 합격을 보장하는 교육이 아닙니다. 공부 방법을 알려 드려요.",
])
def test_copular_negation_of_a_promise_is_not_a_promise(body):
    assert run(body=body)[1] == []


def test_copular_negation_with_a_reason_clause():
    assert run(headline="원금 보장 상품이 아니므로 위험을 확인하세요")[1] == []


@pytest.mark.parametrize("body", [
    "수익을 약속하는 강의입니다.",                                             # 부정이 아님
    "수익을 약속하는 강의가 아니라 확정 수익을 약속하는 강의입니다.",         # 부정하고 곧바로 반대로 약속
    "원금 보장 상품이 아니라고요? 아니요, 원금 보장입니다.",                    # 부정을 흉내 내고 뒤에서 뒤집음
])
def test_negation_lookalikes_are_still_caught(body):
    assert any(r == "AD-GUA-01" for r, _ in run(body=body)[1])


# --------------------------------------------------------------------------- 2. 수법을 소개하며 경계하는 문장
def test_a_sentence_that_warns_about_a_scam_pitch_is_not_the_pitch():
    body = "'신용 무관', '무심사'를 앞세운 대출 광고는 불법 사금융일 수 있으니 주의하세요."
    assert [r for r, _ in run(body=body, category="loan")[1] if r == "AD-FRD-01"] == []


@pytest.mark.parametrize("body", [
    "'무심사'를 앞세운 대출을 지금 신청하세요 (불법 아님)",                    # 판매 문구가 경고 어미를 흉내
    "무심사를 앞세운 저희 대출은 불법이 아닙니다. 주의하실 필요 없어요",        # 판정어를 부정
    "무심사 대출, 지금 바로 신청하세요",                                         # 경고 구조 없음
    "무심사를 앞세운 대출은 사기 같지만 진짜입니다",                              # 경계 동사가 없다
])
def test_scam_pitches_dressed_as_warnings_are_still_caught(body):
    assert any(r == "AD-FRD-01" for r, _ in run(body=body, category="loan")[1])


# --------------------------------------------------------------------------- 3. 상호 속 최상급
def test_a_superlative_that_is_only_part_of_the_brand_name_is_not_a_claim():
    out, found = run(headline="최고수산 제주 갈치 산지 직송", name="가상최고수산", category="ecommerce", image_text="최고수산 | 제주 갈치")
    assert not any(r == "AD-ABS-01" for r, _ in found) and out == "APPROVE"


@pytest.mark.parametrize("headline,name", [
    ("최고수산, 국내 최고 품질", "가상최고수산"),         # 상호 밖에서 다시 주장
    ("최고 품질 갈치", "가상최고수산"),                    # 낱말이 매치와 같다 — 상호가 아니라 주장
    ("최고", "최고"),                                      # 상호 전체가 주장
    ("가상최고수산 최고의 맛", "가상최고수산"),             # '최고의' 는 상호의 낱말이 아니다
    ("국내 1위 수산", "1위수산"),                          # 순위·유일성 표현은 상호에 있어도 주장이다
])
def test_superlative_claims_outside_or_equal_to_the_brand_are_still_caught(headline, name):
    assert any(r == "AD-ABS-01" for r, _ in run(headline=headline, name=name, category="ecommerce")[1])


def test_exceptions_off_means_the_original_behaviour():
    c = Creative.model_validate({"ad_id": "x", "advertiser": {"name": "최고수산", "category": "ecommerce", "verified": True}, "headline": "최고수산 갈치",
                                 "body": "신선한 갈치", "cta": "자세히 보기"})
    assert any(f.rule_id == "AD-ABS-01" for f in review(c, ReviewConfig(exceptions=False)).findings)
