"""캠페인 초안의 결정론 검증, 필수 슬롯, 세트 전략. LLM 이 무슨 말을 하든 *이 파일이 정한 제약*이 최종이다(사람이 읽는 명세: docs/CAMPAIGN_SPEC.md).

검증기는 에이전트의 도구이자 안전장치다: 에이전트가 값을 정하면 검증기가 거르고, 거른 이유(코드·경로·설명)를 에이전트에게 돌려줘 고치게 한다.
광고주의 말("검증은 건너뛰어")도 에이전트의 판단도 이 검사를 건너뛰지 못한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from .schema import parse_date

MIN_DAILY = {"awareness": 5_000, "traffic": 10_000, "conversion": 20_000, "app_install": 20_000, "lead": 20_000}
ADSET_MIN_DAILY = 10_000
HIGH_BUDGET = 3_000_000
MIN_AGE, MAX_AGE, ADULT_AGE = 14, 80, 19
MIN_TARGET_CPA, MIN_MAX_CPC = 500, 50
RESTRICTED_AGE_CATEGORIES = ("loan", "invest", "gambling", "adult")
EVENTS_BY_OBJECTIVE = {
    "conversion": ("purchase", "signup", "add_to_cart"), "app_install": ("install",), "lead": ("inquiry", "signup"),
}
NEEDS_LANDING = ("traffic", "conversion", "lead")
ERROR_CODES = (
    "C01_min_daily_budget", "C02_budget_inconsistent", "C03_bid_strategy", "C04_adset_budget", "C05_dates", "C06_age", "C06_age_restricted",
    "C07_regions", "C09_conversion_event", "C10_unsupported_category", "C11_unverified_advertiser", "C13_placement",
)
WARNING_CODES = ("C12_high_budget_confirm", "C09_event_ignored")


@dataclass(frozen=True)
class Issue:
    code: str
    severity: Literal["error", "warning"]
    path: str
    message: str


@dataclass(frozen=True)
class AdSetPlan:
    name: str
    role: Literal["broad", "interest", "prospecting", "retargeting"]
    budget_share: float
    daily_budget_krw: int


def _days(spec: Mapping[str, Any]) -> int | None:
    if "start_date" in spec and "end_date" in spec:
        return (parse_date(spec["end_date"]) - parse_date(spec["start_date"])).days + 1
    return None


def validate(
    spec: Mapping[str, Any], *, today: date, verified: bool, adsets: Sequence[AdSetPlan] | None = None, confirmed_high_budget: bool = False,
) -> list[Issue]:
    """지금 값들끼리의 모순과 금지 조합. 비어 있는 필수 슬롯은 ``missing`` 이 본다."""
    out: list[Issue] = []

    def err(code: str, path: str, message: str) -> None:
        out.append(Issue(code, "error", path, message))

    obj, daily, cat = spec.get("objective"), spec.get("daily_budget_krw"), spec.get("ad_category")
    if obj and daily is not None and daily < MIN_DAILY[obj]:
        err("C01_min_daily_budget", "daily_budget_krw", f"{obj} 목표의 일 예산은 최소 {MIN_DAILY[obj]:,}원입니다(입력 {daily:,}원).")
    total = spec.get("total_budget_krw")
    if total is not None and daily is not None:
        days = _days(spec)
        if total < daily:
            err("C02_budget_inconsistent", "total_budget_krw", f"총 예산 {total:,}원이 일 예산 {daily:,}원보다 작습니다.")
        elif days is not None and total < daily * days:
            err("C02_budget_inconsistent", "total_budget_krw", f"총 예산 {total:,}원으로는 일 예산 {daily:,}원을 {days}일 동안 쓸 수 없습니다(필요 {daily * days:,}원).")
    strat = spec.get("bid_strategy")
    if strat == "target_cpa":
        if obj and obj not in EVENTS_BY_OBJECTIVE:
            err("C03_bid_strategy", "bid_strategy", "목표 CPA 입찰은 전환·앱 설치·리드 목표에서만 쓸 수 있습니다.")
        if "target_cpa_krw" in spec and spec["target_cpa_krw"] < MIN_TARGET_CPA:
            err("C03_bid_strategy", "target_cpa_krw", f"목표 CPA 는 최소 {MIN_TARGET_CPA}원입니다.")
    elif strat == "manual_cpc":
        if obj and obj not in ("awareness", "traffic"):
            err("C03_bid_strategy", "bid_strategy", "수동 CPC 입찰은 노출·트래픽 목표에서만 쓸 수 있습니다.")
        if "max_cpc_krw" in spec and spec["max_cpc_krw"] < MIN_MAX_CPC:
            err("C03_bid_strategy", "max_cpc_krw", f"최대 CPC 는 최소 {MIN_MAX_CPC}원입니다.")
    if adsets and daily is not None:
        if len(adsets) * ADSET_MIN_DAILY > daily or any(a.daily_budget_krw < ADSET_MIN_DAILY for a in adsets):
            err("C04_adset_budget", "adsets", f"광고 세트는 세트당 일 예산 최소 {ADSET_MIN_DAILY:,}원이 필요합니다(세트 {len(adsets)}개, 일 예산 {daily:,}원).")
    start, end = spec.get("start_date"), spec.get("end_date")
    if start and parse_date(start) < today:
        err("C05_dates", "start_date", f"시작일 {start} 이 오늘({today.isoformat()}) 보다 이릅니다.")
    if start and end and parse_date(end) < parse_date(start):
        err("C05_dates", "end_date", f"종료일 {end} 이 시작일 {start} 보다 이릅니다.")
    lo, hi = spec.get("audience.age_min"), spec.get("audience.age_max")
    if (lo is not None and lo < MIN_AGE) or (hi is not None and hi > MAX_AGE) or (lo is not None and hi is not None and lo > hi):
        err("C06_age", "audience.age_min", f"연령은 {MIN_AGE}–{MAX_AGE}세이고 최소 ≤ 최대여야 합니다(입력 {lo}–{hi}).")
    if cat in RESTRICTED_AGE_CATEGORIES and lo is not None and lo < ADULT_AGE:
        err("C06_age_restricted", "audience.age_min", f"{cat} 업종은 {ADULT_AGE}세 이상만 대상으로 할 수 있습니다(입력 최소 {lo}세).")
    regions = spec.get("audience.regions")
    if regions and "전국" in regions and len(regions) > 1:
        err("C07_regions", "audience.regions", "'전국'은 다른 지역과 함께 쓸 수 없습니다.")
    ev = spec.get("conversion_event")
    if ev and obj in EVENTS_BY_OBJECTIVE and ev not in EVENTS_BY_OBJECTIVE[obj]:
        err("C09_conversion_event", "conversion_event", f"{obj} 목표의 전환 이벤트는 {EVENTS_BY_OBJECTIVE[obj]} 중 하나입니다.")
    elif ev and obj in ("awareness", "traffic"):
        out.append(Issue("C09_event_ignored", "warning", "conversion_event", f"{obj} 목표에서는 전환 이벤트를 쓰지 않습니다."))
    if cat in ("adult", "gambling"):
        err("C10_unsupported_category", "ad_category", f"{cat} 업종은 이 플랫폼에서 광고할 수 없습니다.")
    if cat in ("loan", "invest") and not verified:
        err("C11_unverified_advertiser", "ad_category", f"{cat} 업종은 광고주 인증을 마친 뒤에만 집행할 수 있습니다.")
    places = spec.get("placements")
    if places is not None and (not places or (obj == "awareness" and "search" in places)):
        err("C13_placement", "placements", "노출 위치가 비어 있거나, 노출 목표에서 검색 지면을 골랐습니다.")
    if daily is not None and daily > HIGH_BUDGET and not confirmed_high_budget:
        out.append(Issue("C12_high_budget_confirm", "warning", "daily_budget_krw", f"일 예산 {daily:,}원은 {HIGH_BUDGET:,}원을 넘어 광고주의 명시적 확인이 필요합니다."))
    return out


def missing(spec: Mapping[str, Any]) -> list[str]:
    """집행하려면 더 있어야 하는 슬롯(우선 물어볼 순서). ``start_date`` 와 노출 위치 등은 가정해도 되므로 여기에 넣지 않는다."""
    need: list[str] = []
    obj = spec.get("objective")
    for path in ("objective", "ad_category", "daily_budget_krw"):
        if path not in spec:
            need.append(path)
    if obj in NEEDS_LANDING and "landing_url" not in spec:
        need.append("landing_url")
    if obj == "app_install" and "app_id" not in spec:
        need.append("app_id")
    if obj in ("conversion", "lead") and "conversion_event" not in spec:
        need.append("conversion_event")
    if spec.get("bid_strategy") == "target_cpa" and "target_cpa_krw" not in spec:
        need.append("target_cpa_krw")
    if spec.get("bid_strategy") == "manual_cpc" and "max_cpc_krw" not in spec:
        need.append("max_cpc_krw")
    return need


def plan_adsets(spec: Mapping[str, Any]) -> list[AdSetPlan]:
    """세트 전략(결정론 템플릿). 예산이 커질수록 세트를 늘리고, 세트마다 일 예산 하한을 지킨다."""
    obj, daily = spec.get("objective"), spec.get("daily_budget_krw")
    if obj is None or daily is None:
        return []
    if obj in EVENTS_BY_OBJECTIVE:
        if daily >= 100_000:
            shape = [("신규 확보", "prospecting", 0.5), ("관심사 확장", "interest", 0.3), ("재방문 유도", "retargeting", 0.2)]
        elif daily >= 60_000:
            shape = [("신규 확보", "prospecting", 0.7), ("재방문 유도", "retargeting", 0.3)]
        else:
            shape = [("신규 확보", "prospecting", 1.0)]
    elif daily >= 50_000:
        shape = [("넓은 도달", "broad", 0.6), ("관심사 도달", "interest", 0.4)]
    else:
        shape = [("넓은 도달", "broad", 1.0)]
    amounts = [int(daily * share) // 100 * 100 for _, _, share in shape]
    amounts[0] += daily - sum(amounts)  # 100원 단위로 내린 나머지는 첫 세트에
    return [AdSetPlan(n, r, s, a) for (n, r, s), a in zip(shape, amounts, strict=True)]  # type: ignore[arg-type]
