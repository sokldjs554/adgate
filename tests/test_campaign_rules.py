"""캠페인 슬롯 정규화와 결정론 검증기·세트 전략. 제약 하나하나를 경계값으로 시험한다."""

from __future__ import annotations

from datetime import date

import pytest

from adgate.campaign.rules import (
    ADSET_MIN_DAILY,
    ERROR_CODES,
    MIN_DAILY,
    AdSetPlan,
    missing,
    plan_adsets,
    validate,
)
from adgate.campaign.schema import SlotError, normalize, parse_date

TODAY = date(2026, 10, 15)


def codes(spec, **kw):
    kw.setdefault("today", TODAY)
    kw.setdefault("verified", True)
    return [i.code for i in validate(spec, **kw)]


# --------------------------------------------------------------------------- 정규화
def test_normalize_accepts_canonical_values_and_rejects_everything_else():
    assert normalize("objective", "conversion") == "conversion"
    assert normalize("daily_budget_krw", 50_000) == 50_000
    assert normalize("audience.regions", ["서울", "경기"]) == ["서울", "경기"]
    assert normalize("landing_url", "https://shop.example.com/event?a=1") == "https://shop.example.com/event?a=1"
    assert normalize("start_date", "2026-10-16") == "2026-10-16"
    for path, bad in [
        ("objective", "sales"), ("daily_budget_krw", "5만원"), ("daily_budget_krw", -1), ("daily_budget_krw", True),
        ("audience.regions", ["서울", "서울"]), ("audience.regions", ["달나라"]), ("audience.genders", "female"),
        ("landing_url", "http://insecure.example.com"), ("landing_url", "javascript:alert(1)"), ("landing_url", "https://localhost"),
        ("start_date", "2026-13-01"), ("start_date", "10월 16일"), ("nope", 1), ("app_id", "  "),
    ]:
        with pytest.raises(SlotError):
            normalize(path, bad)
    with pytest.raises(SlotError):
        parse_date("2026-02-30")


# --------------------------------------------------------------------------- 제약(경계값)
@pytest.mark.parametrize("objective", list(MIN_DAILY))
def test_c01_minimum_daily_budget_per_objective(objective):
    lo = MIN_DAILY[objective]
    assert "C01_min_daily_budget" in codes({"objective": objective, "daily_budget_krw": lo - 1})
    assert "C01_min_daily_budget" not in codes({"objective": objective, "daily_budget_krw": lo})


def test_c02_total_budget_must_be_able_to_fund_the_daily_budget_for_the_whole_period():
    base = {"objective": "traffic", "daily_budget_krw": 50_000, "start_date": "2026-10-16", "end_date": "2026-10-25"}      # 10일
    assert "C02_budget_inconsistent" in codes({**base, "total_budget_krw": 499_999})
    assert "C02_budget_inconsistent" not in codes({**base, "total_budget_krw": 500_000})
    assert "C02_budget_inconsistent" in codes({"objective": "traffic", "daily_budget_krw": 50_000, "total_budget_krw": 40_000})   # 기간이 없어도 총예산 < 일예산


def test_c03_bid_strategy_depends_on_the_objective_and_minimums():
    assert "C03_bid_strategy" in codes({"objective": "awareness", "bid_strategy": "target_cpa"})
    assert "C03_bid_strategy" in codes({"objective": "conversion", "bid_strategy": "manual_cpc"})
    assert "C03_bid_strategy" in codes({"objective": "conversion", "bid_strategy": "target_cpa", "target_cpa_krw": 499})
    assert "C03_bid_strategy" not in codes({"objective": "conversion", "bid_strategy": "target_cpa", "target_cpa_krw": 500})
    assert "C03_bid_strategy" in codes({"objective": "traffic", "bid_strategy": "manual_cpc", "max_cpc_krw": 49})
    assert "C03_bid_strategy" not in codes({"objective": "traffic", "bid_strategy": "manual_cpc", "max_cpc_krw": 50})


def test_c04_every_adset_needs_its_own_minimum_daily_budget():
    spec = {"objective": "conversion", "daily_budget_krw": 25_000}
    two = [AdSetPlan("a", "prospecting", 0.7, 17_500), AdSetPlan("b", "retargeting", 0.3, 7_500)]
    assert "C04_adset_budget" in codes(spec, adsets=two)
    assert "C04_adset_budget" not in codes({**spec, "daily_budget_krw": 60_000}, adsets=plan_adsets({**spec, "daily_budget_krw": 60_000}))


def test_c05_dates_cannot_be_in_the_past_or_backwards():
    assert "C05_dates" in codes({"start_date": "2026-10-14"})
    assert "C05_dates" not in codes({"start_date": "2026-10-15"})
    assert "C05_dates" in codes({"start_date": "2026-10-20", "end_date": "2026-10-19"})
    assert "C05_dates" not in codes({"start_date": "2026-10-20", "end_date": "2026-10-20"})


def test_c06_age_bounds_and_restricted_categories():
    assert "C06_age" in codes({"audience.age_min": 13}) and "C06_age" in codes({"audience.age_max": 81})
    assert "C06_age" in codes({"audience.age_min": 40, "audience.age_max": 30})
    assert "C06_age_restricted" in codes({"ad_category": "loan", "audience.age_min": 18})
    assert "C06_age_restricted" not in codes({"ad_category": "loan", "audience.age_min": 19})
    assert "C06_age_restricted" not in codes({"ad_category": "cosmetic", "audience.age_min": 14})


def test_c07_nationwide_cannot_be_combined_with_regions():
    assert "C07_regions" in codes({"audience.regions": ["전국", "서울"]})
    assert "C07_regions" not in codes({"audience.regions": ["전국"]}) and "C07_regions" not in codes({"audience.regions": ["서울", "경기"]})


def test_c09_conversion_event_must_fit_the_objective():
    assert "C09_conversion_event" in codes({"objective": "conversion", "conversion_event": "install"})
    assert "C09_conversion_event" in codes({"objective": "app_install", "conversion_event": "purchase"})
    assert "C09_conversion_event" not in codes({"objective": "lead", "conversion_event": "inquiry"})
    assert "C09_event_ignored" in codes({"objective": "traffic", "conversion_event": "purchase"})


def test_c10_c11_account_level_blocks():
    assert "C10_unsupported_category" in codes({"ad_category": "gambling"}) and "C10_unsupported_category" in codes({"ad_category": "adult"})
    assert "C11_unverified_advertiser" in codes({"ad_category": "invest"}, verified=False)
    assert "C11_unverified_advertiser" not in codes({"ad_category": "invest"}, verified=True)
    assert "C11_unverified_advertiser" not in codes({"ad_category": "cosmetic"}, verified=False)


def test_c12_high_budget_needs_explicit_confirmation_and_c13_placements():
    big = {"objective": "conversion", "daily_budget_krw": 3_000_001}
    issue = [i for i in validate(big, today=TODAY, verified=True) if i.code == "C12_high_budget_confirm"]
    assert issue and issue[0].severity == "warning"
    assert "C12_high_budget_confirm" not in codes(big, confirmed_high_budget=True)
    assert "C12_high_budget_confirm" not in codes({**big, "daily_budget_krw": 3_000_000})
    assert "C13_placement" in codes({"placements": []}) and "C13_placement" in codes({"objective": "awareness", "placements": ["search"]})
    assert "C13_placement" not in codes({"objective": "traffic", "placements": ["search"]})


def test_every_documented_error_code_is_produced_by_some_input():
    seen = set()
    probes = [
        {"objective": "traffic", "daily_budget_krw": 1}, {"daily_budget_krw": 5, "total_budget_krw": 1}, {"objective": "awareness", "bid_strategy": "target_cpa"},
        {"start_date": "2026-01-01"}, {"audience.age_min": 1}, {"ad_category": "loan", "audience.age_min": 15}, {"audience.regions": ["전국", "서울"]},
        {"objective": "conversion", "conversion_event": "install"}, {"ad_category": "adult"}, {"placements": []},
    ]
    for spec in probes:
        seen |= {i.code for i in validate(spec, today=TODAY, verified=False) if i.severity == "error"}
    seen |= {i.code for i in validate({"ad_category": "invest"}, today=TODAY, verified=False)}
    seen |= {i.code for i in validate({"objective": "conversion", "daily_budget_krw": 25_000}, today=TODAY, verified=True,
                                      adsets=[AdSetPlan("a", "prospecting", 0.5, 5_000)] * 3)}
    assert set(ERROR_CODES) <= seen, set(ERROR_CODES) - seen


# --------------------------------------------------------------------------- 필수 슬롯과 세트 전략
def test_missing_slots_depend_on_the_objective_in_asking_order():
    assert missing({}) == ["objective", "ad_category", "daily_budget_krw"]
    assert missing({"objective": "traffic", "ad_category": "ecommerce", "daily_budget_krw": 20_000}) == ["landing_url"]
    assert missing({"objective": "conversion", "ad_category": "ecommerce", "daily_budget_krw": 50_000}) == ["landing_url", "conversion_event"]
    assert missing({"objective": "app_install", "ad_category": "app", "daily_budget_krw": 50_000}) == ["app_id"]
    assert missing({"objective": "awareness", "ad_category": "app", "daily_budget_krw": 9_000}) == []
    assert missing({"objective": "lead", "ad_category": "education", "daily_budget_krw": 30_000, "landing_url": "https://a.example.com", "conversion_event": "inquiry",
                    "bid_strategy": "target_cpa"}) == ["target_cpa_krw"]


@pytest.mark.parametrize(
    ("objective", "daily", "roles"),
    [
        ("conversion", 20_000, ["prospecting"]), ("conversion", 59_999, ["prospecting"]), ("conversion", 60_000, ["prospecting", "retargeting"]),
        ("lead", 99_999, ["prospecting", "retargeting"]), ("app_install", 100_000, ["prospecting", "interest", "retargeting"]),
        ("traffic", 49_999, ["broad"]), ("awareness", 50_000, ["broad", "interest"]),
    ],
)
def test_adset_strategy_grows_with_budget_and_always_respects_the_per_set_minimum(objective, daily, roles):
    plan = plan_adsets({"objective": objective, "daily_budget_krw": daily})
    assert [p.role for p in plan] == roles
    assert sum(p.daily_budget_krw for p in plan) == daily and all(p.daily_budget_krw >= ADSET_MIN_DAILY for p in plan)
    assert abs(sum(p.budget_share for p in plan) - 1.0) < 1e-9
    assert not any(i.code == "C04_adset_budget" for i in validate({"objective": objective, "daily_budget_krw": daily}, today=TODAY, verified=True, adsets=plan))
    assert plan_adsets({}) == []
