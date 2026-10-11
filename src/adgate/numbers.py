"""광고 문구 속 수치 주장(가격·비율·기간)을 뽑고 랜딩 페이지와 대조한다.

값은 종류(``won`` · ``pct`` · ``time``)별로 비교한다. 5만원 = 50,000원, 5천만원 = 50,000,000원,
3만 9천 원 = 39,000원(조각을 더한다), 천원 = 1,000원(숫자 없이 단위만 있는 금액).

모든 정규식은 입력 길이에 선형이어야 한다(독립 리뷰가 지수·2차 시간 입력을 찾았다 — docs/REVIEW.md C1·C2). 그래서
* 숫자는 앞이 숫자·쉼표·점이 아닌 자리에서만 시작하고(``(?<![\\d,.])``) 자릿수를 제한한다,
* 금액은 '원' 을 먼저 찾고 그 앞 짧은 구간(``_MONEY_WINDOW``)만 거꾸로 읽는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d{1,15}(?:\.\d{1,9})?)"
_MULT_RX = r"(?:억|천만|만|천|백)"
# 금액 = (숫자 단위)* (숫자 [단위] | 단위) — 단위만 있는 조각("천원")은 앞이 한글이 아닐 때만 쓴다("수만 원", "이만원" 은 정확한 값이 아니다).
_MONEY_TAIL = re.compile(
    rf"(?<![\d,.])((?:{_NUM}\s*{_MULT_RX}\s*)*(?:{_NUM}\s*{_MULT_RX}?|(?<![가-힣]){_MULT_RX}))\s*$"
)
_MONEY_PART = re.compile(rf"({_NUM})?\s*({_MULT_RX})?")
_MONEY_WINDOW = 48
_PCT = re.compile(r"(?<![\d,.])((?:\d{1,3}(?:,\d{3})+|\d{1,12})(?:\.\d{1,9})?)\s*%")
_TIME = re.compile(r"(?<![\d,.])(\d{1,9})\s*(개월|달|일)")
# '1일 1회' 처럼 복용·이용 횟수를 말하는 '일' 은 기간 주장이 아니다.
_FREQUENCY_AFTER = re.compile(r"^\s*(?:\d+\s*(?:회|정|알|포|캡슐|번|봉|스푼|개(?!월))|한\s*번|두\s*번)")
_MULT = {None: 1, "": 1, "백": 100, "천": 1_000, "만": 10_000, "천만": 10_000_000, "억": 100_000_000}


@dataclass(frozen=True)
class Claim:
    kind: str                  # won | pct | time
    value: tuple[object, ...]  # 비교용 정규화 값
    start: int                 # soft 문자열 안의 위치
    end: int


def _won_claims(soft: str) -> list[Claim]:
    claims: list[Claim] = []
    pos = soft.find("원")
    while pos != -1:
        m = _MONEY_TAIL.search(soft, max(0, pos - _MONEY_WINDOW), pos)
        if m is not None:
            value = 0
            for part in _MONEY_PART.finditer(m.group(1)):
                digits, unit = part.group(1), part.group(2)
                if digits is None and unit is None:
                    continue
                value += round((float(digits.replace(",", "")) if digits else 1) * _MULT[unit])
            claims.append(Claim("won", (value,), m.start(1), pos + 1))
        pos = soft.find("원", pos + 1)
    return claims


def _is_date_day(soft: str, start: int) -> bool:
    """'10월 31일' 의 '31일' 처럼 바로 앞(공백 건너뛰고)에 '월' 이 오는가."""
    i = start - 1
    while i >= 0 and soft[i].isspace():
        i -= 1
    return i >= 0 and soft[i] == "월"


def extract(soft: str) -> list[Claim]:
    claims = _won_claims(soft)
    for m in _PCT.finditer(soft):
        claims.append(Claim("pct", (round(float(m.group(1).replace(",", "")), 6),), m.start(), m.end()))
    for m in _TIME.finditer(soft):
        unit = {"달": "개월"}.get(m.group(2), m.group(2))
        if unit == "일" and (_is_date_day(soft, m.start()) or _FREQUENCY_AFTER.match(soft, m.end())):
            continue  # 10월 31일 같은 날짜, 1일 1회 같은 횟수는 기간 주장이 아니다
        claims.append(Claim("time", (int(m.group(1)), unit), m.start(), m.end()))
    claims.sort(key=lambda c: c.start)
    return claims


def landing_values(soft: str) -> dict[str, set[tuple[object, ...]]]:
    values: dict[str, set[tuple[object, ...]]] = {"won": set(), "pct": set(), "time": set()}
    for c in extract(soft):
        values[c.kind].add(c.value)
    return values
