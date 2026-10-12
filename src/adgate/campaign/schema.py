"""가상 광고 플랫폼의 캠페인 사양(슬롯)과 값 정규화. 사람이 읽는 명세는 docs/CAMPAIGN_SPEC.md 이고 이 파일이 같은 값을 코드로 둔다(테스트가 둘의 일치를 검사한다).

캠페인 초안은 ``dict[str, Any]`` 로 다룬다 — 키는 점으로 이은 슬롯 경로(``audience.age_min``). 값이 비어 있으면 키가 없다.
각 슬롯에는 *누가 정한 값인가*(``source``)가 따라붙는다: ``user``(광고주가 말했고 발화에 인용이 있다) / ``derived``(광고주 말에서 계산했다) / ``assumed``(비워 둬서 기본값을 넣었다 — 확인이 필요하다).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

OBJECTIVES = ("awareness", "traffic", "conversion", "app_install", "lead")
CATEGORIES = ("loan", "invest", "supplement", "cosmetic", "ecommerce", "app", "education", "general", "adult", "gambling")
BID_STRATEGIES = ("lowest_cost", "target_cpa", "manual_cpc")
CONVERSION_EVENTS = ("purchase", "signup", "add_to_cart", "install", "inquiry")
PLACEMENTS = ("feed", "banner", "story", "search")
GENDERS = ("female", "male")
REGIONS = (
    "전국", "서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산", "세종", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
)
INTERESTS = (
    "패션", "뷰티", "푸드", "여행", "금융", "게임", "헬스", "육아", "교육", "자동차", "가전", "반려동물", "인테리어", "문화", "스포츠", "부동산",
    "재테크", "앱·소프트웨어", "건강식품", "취업",
)
Source = Literal["user", "derived", "assumed"]

# 경로 → 타입 이름. 값 검사는 ``normalize`` 가 한다.
SLOTS: dict[str, str] = {
    "objective": "enum", "ad_category": "enum", "daily_budget_krw": "int", "total_budget_krw": "int", "start_date": "date", "end_date": "date",
    "audience.age_min": "int", "audience.age_max": "int", "audience.genders": "enum_list", "audience.regions": "enum_list",
    "audience.interests": "enum_list", "bid_strategy": "enum", "target_cpa_krw": "int", "max_cpc_krw": "int", "placements": "enum_list",
    "conversion_event": "enum", "landing_url": "url", "app_id": "str",
}
ENUMS: dict[str, tuple[str, ...]] = {
    "objective": OBJECTIVES, "ad_category": CATEGORIES, "bid_strategy": BID_STRATEGIES, "conversion_event": CONVERSION_EVENTS,
    "placements": PLACEMENTS, "audience.genders": GENDERS, "audience.regions": REGIONS, "audience.interests": INTERESTS,
}
# 광고주가 비워 뒀을 때 어시스턴트가 *가정*해도 되는 값(가정은 표시하고 확인을 받는다)
ASSUMABLE_DEFAULTS: dict[str, Any] = {
    "placements": ["feed", "banner"], "audience.genders": [], "audience.regions": ["전국"], "audience.age_min": 19, "audience.age_max": 65,
    "bid_strategy": "lowest_cost",
}
_URL = re.compile(r"^https://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:[/?#][^\s]*)?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_INT = 10_000_000_000


class SlotError(ValueError):
    """슬롯 값이 형식에 맞지 않는다(경로를 모르거나, 열거형 밖이거나, 날짜·URL 형식이 아니다)."""


def parse_date(text: str) -> date:
    if not _DATE.match(text):
        raise SlotError(f"날짜는 YYYY-MM-DD: {text!r}")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise SlotError(f"없는 날짜: {text!r}") from exc


def normalize(path: str, value: Any) -> Any:
    """슬롯 값을 표준형으로 바꾼다. 형식이 맞지 않으면 ``SlotError`` (조용히 고쳐 주지 않는다)."""
    kind = SLOTS.get(path)
    if kind is None:
        raise SlotError(f"알 수 없는 슬롯: {path}")
    if kind == "enum":
        if value not in ENUMS[path]:
            raise SlotError(f"{path} 는 {ENUMS[path]} 중 하나: {value!r}")
        return value
    if kind == "enum_list":
        if not isinstance(value, list) or any(v not in ENUMS[path] for v in value) or len(set(map(str, value))) != len(value):
            raise SlotError(f"{path} 는 {ENUMS[path]} 의 중복 없는 목록: {value!r}")
        return list(value)
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_INT:
            raise SlotError(f"{path} 는 0 이상의 정수: {value!r}")
        return value
    if kind == "date":
        if not isinstance(value, str):
            raise SlotError(f"{path} 는 YYYY-MM-DD 문자열: {value!r}")
        parse_date(value)
        return value
    if kind == "url":
        if not isinstance(value, str) or not _URL.match(value) or len(value) > 300:
            raise SlotError(f"{path} 는 https URL: {value!r}")
        return value
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise SlotError(f"{path} 는 비어 있지 않은 문자열: {value!r}")
    return value.strip()
