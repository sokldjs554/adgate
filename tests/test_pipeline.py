from __future__ import annotations

import time

from conftest import mk
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from adgate.config import ReviewConfig
from adgate.models import ALL_TEXT_FIELDS
from adgate.pipeline import review

ALPHABET = list(
    "수익보장무조건승인 .-_*​‌１２３０％%abNo!?\n가나다암예방치료최고1위오늘만타사대비의사추천원금손실없는연월일개월원"
    "😊✨🌿100"
)
CATEGORIES = ["loan", "invest", "supplement", "cosmetic", "ecommerce", "app", "education", "general", "adult", "gambling"]
RANK = {"APPROVE": 0, "REVIEW": 1, "REJECT": 2}


def stable(result):
    d = result.model_dump()
    d["stats"]["latency_ms"] = 0
    return d


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(alphabet=ALPHABET, max_size=90), st.text(alphabet=ALPHABET, max_size=90), st.sampled_from(CATEGORIES), st.booleans())
def test_every_quote_is_an_exact_slice_of_the_original_field(a, b, category, verified):
    c = mk(a, b, "버튼", disclosure=a[::-1], image_text=b[:30], landing=a + b, category=category, verified=verified)
    r = review(c)
    for f in r.findings:
        if f.quote is not None:
            assert f.field in ALL_TEXT_FIELDS and c.text(f.field)[f.start:f.end] == f.quote


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(alphabet=ALPHABET, max_size=90), st.sampled_from(CATEGORIES))
def test_review_is_deterministic_and_the_outcome_follows_the_findings(text, category):
    c = mk(text, text, category=category, disclosure=text)
    r1, r2 = review(c), review(c)
    assert stable(r1) == stable(r2)
    severities = {f.severity for f in r1.findings}
    expected = "REJECT" if "reject" in severities else "REVIEW" if "review" in severities else "APPROVE"
    assert r1.outcome == expected


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(alphabet=ALPHABET, max_size=90), st.sampled_from(CATEGORIES))
def test_the_challenger_can_only_make_a_decision_more_conservative(text, category):
    c = mk(text, text[::-1], category=category)
    assert RANK[review(c).outcome] >= RANK[review(c, ReviewConfig(challenge=False)).outcome]


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(max_size=120))
def test_arbitrary_unicode_never_crashes(text):
    review(mk(text, text, text, disclosure=text, image_text=text, landing=text, category="loan"))


def test_exceptions_off_never_approves_something_the_default_rejects():
    # 예외를 끄면 더 많이 잡을 뿐이다(정상 소재 오반려는 늘 수 있지만 위반 소재를 놓치지 않는다).
    for h in ("무조건 승인", "수익을 보장하지 않습니다", "최고 연 5.9%", "암 예방", "100% 환불 보장"):
        c = mk(h, category="invest", disclosure="투자 상품은 원금 손실이 발생할 수 있습니다.")
        assert RANK[review(c, ReviewConfig(exceptions=False)).outcome] >= RANK[review(c).outcome]


WORST_CASES = {
    "한 글자 반복": "가" * 2000,
    "구분 기호 반복": ". " * 1000,
    "보장 구절 반복": "수익보장" * 500,
    "제로폭 반복": "​" * 2000,
    "공백과 글자": "수 익 보 장 " * 330,
    "숫자와 단위": "1.5% 3,900원 7일 " * 160,
    "질병 반복": "암 예방 " * 400,
}


def test_adversarial_long_fields_finish_quickly():
    for name, text in WORST_CASES.items():
        c = mk(text[:2000], text[:2000], text[:2000], disclosure=text[:2000], image_text=text[:2000], landing=text[:20000], category="supplement")
        t0 = time.perf_counter()
        review(c)
        elapsed = time.perf_counter() - t0
        assert elapsed < 3.0, f"{name}: {elapsed:.2f}s"


def test_repeated_violations_cannot_inflate_the_output():
    c = mk("수익보장 " * 400, category="invest", disclosure="투자 상품은 원금 손실이 발생할 수 있습니다.")
    r = review(c)
    assert r.outcome == "REJECT"                       # 판정은 전체 발견으로 계산한다
    assert len(r.findings) <= 100 and len(r.reasons) <= 100
    assert r.stats.findings_truncated > 100
    assert len(r.model_dump_json()) < 120_000


def test_truncation_keeps_the_heaviest_findings():
    # 보류 등급 발견이 수백 개여도 반려 등급 하나는 남는다
    c = mk("오늘만 " * 300 + "무조건 승인", category="loan", disclosure="신용점수 하락 가능. 연 9.0%")
    r = review(c)
    assert r.outcome == "REJECT" and any(f.severity == "reject" for f in r.findings)
